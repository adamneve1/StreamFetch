import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fakeredis
from app import storage, transcript_reader, web


DESCRIPTION = ('Program: Dialog Batam\nHari/Tanggal: Minggu, 4 Oktober 2026\n'
               'Jam: 09.00 WIB\nTema: Pendidikan inklusif\nNarasumber:\n'
               '1. Dr. Rina (Dosen)\n2. Budi — Kepala sekolah\n'
               'Presenter: Sari\n\nSeluruh deskripsi asli.\nhttps://example.com\n')


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, DATA_DIR=self.tmp.name,
                              DOWNLOAD_DIR=self.tmp.name, WEB_PASSWORD='reader-test',
                              WEB_SECRET_KEY='test-secret')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.row = {'job_id': 'reader-job', 'source': 'youtube', 'filename': 'reader.mp4',
                    'source_metadata': {'title': 'Video title', 'description': DESCRIPTION}}
        storage.save_recording(self.row, 'ready')
        self.root.joinpath('reader.txt').write_text('Halo dunia\nSelamat pagi\n')
        self.root.joinpath('reader.srt').write_text(
            '1\n00:00:01,250 --> 00:00:02,500\nHalo dunia\n\n'
            '2\n00:00:05,000 --> 00:00:08,000\nSelamat pagi\n')
        storage.save_transcription_state('reader-job', 'completed',
                                         txt_filename='reader.txt', srt_filename='reader.srt')
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.client = web.create_app(self.redis).test_client()
        self.client.post('/api/login', json={'password': 'reader-test'})

    def data(self):
        return self.client.get('/api/recordings/reader-job/transcript/data')

    def test_explicit_program_fields_and_guest_roles(self):
        parsed = transcript_reader.parse_source_metadata(DESCRIPTION)
        self.assertEqual(parsed['program'], 'Dialog Batam')
        self.assertEqual(parsed['date_time'], 'Minggu, 4 Oktober 2026 · 09.00 WIB')
        self.assertEqual(parsed['theme'], 'Pendidikan inklusif')
        self.assertEqual(parsed['presenter'], 'Sari')
        self.assertEqual([(guest['name'], guest['role']) for guest in parsed['guests']],
                         [('Dr. Rina', 'Dosen'), ('Budi', 'Kepala sekolah')])

    def test_raw_description_persisted_verbatim_and_returned(self):
        self.assertEqual(storage.recording('reader-job')['source_metadata']['description'],
                         DESCRIPTION)
        self.assertEqual(self.data().json['info']['description'], DESCRIPTION)

    def test_existing_sidecars_and_export_routes_are_unchanged(self):
        data = self.data().json
        self.assertEqual(data['exports'], ['txt', 'srt'])
        self.assertEqual(data['segments'], [
            {'start': 1.25, 'end': 2.5, 'text': 'Halo dunia'},
            {'start': 5.0, 'end': 8.0, 'text': 'Selamat pagi'}])
        self.assertEqual(self.client.get('/api/recordings/reader-job/transcript/txt').text,
                         data['raw'])

    def test_missing_metadata_non_youtube_and_txt_only(self):
        storage.delete_recording('reader-job')
        storage.save_recording({'job_id': 'reader-job', 'source': 'oryx',
                                'source_name': 'RRI Batam', 'filename': 'reader.mp4'}, 'ready')
        storage.save_transcription_state('reader-job', 'completed', txt_filename='reader.txt')
        data = self.data().json
        self.assertEqual(data['info']['program'], 'RRI Batam')
        self.assertEqual(data['info']['description'], '')
        self.assertEqual(data['info']['guests'], [])
        self.assertIsNone(data['segments'][0]['start'])
        self.assertEqual(data['exports'], ['txt'])

    def test_no_facts_inferred_from_unlabelled_description(self):
        info = transcript_reader.recording_info({'source_metadata': {
            'title': 'Judul', 'description': 'Sari membahas tema pendidikan dengan Budi.'}})
        self.assertEqual(info['program'], 'Judul')
        self.assertEqual(info['theme'], '')
        self.assertEqual(info['presenter'], '')

    def test_vtt_cue_ids_multiline_and_settings(self):
        cues = transcript_reader.parse_subtitles(
            'WEBVTT\n\nNOTE metadata\nignore me\n\ncue-1\n'
            '01:02.500 --> 01:04.000 align:start\nBaris satu\nBaris dua\n')
        self.assertEqual(cues, [{'start': 62.5, 'end': 64, 'text': 'Baris satu\nBaris dua'}])

    def test_vtt_only_and_malformed_subtitles_keep_legacy_text(self):
        self.root.joinpath('only.vtt').write_text(
            'WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nVTT lama\n')
        row = {'job_id': 'old', 'transcript': {'vtt_filename': 'only.vtt'}}
        data = transcript_reader.reader_data(row, self.root)
        self.assertEqual(data['exports'], ['vtt'])
        self.assertEqual(data['segments'][0]['start'], 3)
        self.assertEqual(data['raw'], 'VTT lama')
        self.root.joinpath('reader.srt').write_text('broken timestamps')
        data = self.data().json
        self.assertEqual(data['raw'], 'Halo dunia\nSelamat pagi\n')
        self.assertIsNone(data['segments'][0]['start'])

    def test_upload_date_is_distinguished_from_program_date(self):
        info = transcript_reader.recording_info({'source_metadata': {
            'title': 'Program', 'upload_date': '20261004'}})
        self.assertEqual(info['date_time'], '2026-10-04')
        self.assertEqual(info['date_time_label'], 'Source upload date')

    def test_unsafe_sidecar_path_never_read(self):
        row = {'job_id': 'unsafe', 'transcript': {'txt_filename': '../secret.txt'}}
        self.assertEqual(transcript_reader.reader_data(row, self.root)['exports'], [])
        self.root.joinpath('link.txt').symlink_to(self.root / 'reader.txt')
        row['transcript']['txt_filename'] = 'link.txt'
        self.assertEqual(transcript_reader.reader_data(row, self.root)['exports'], [])

    def test_reader_is_authenticated_and_never_enqueues(self):
        anonymous = web.create_app(self.redis).test_client()
        self.assertEqual(anonymous.get('/api/recordings/reader-job/transcript/view').status_code, 401)
        self.assertEqual(anonymous.get('/api/recordings/reader-job/transcript/data').status_code, 401)
        self.assertIn('Search transcript', self.client.get(
            '/api/recordings/reader-job/transcript/view').text)
        self.data()
        self.assertEqual(self.redis.llen('transcription_queue'), 0)

    def test_metadata_capture_retains_description_on_partial_refresh(self):
        os.environ.setdefault('TELEGRAM_BOT_TOKEN', '123456:TEST_TOKEN')
        from app import worker
        job = {'source': 'youtube'}
        worker.update_youtube_metadata(job, {'title': 'Judul', 'description': DESCRIPTION})
        worker.update_youtube_metadata(job, {'title': 'Judul baru'})
        storage.save_recording(dict(job, job_id='metadata-job'), 'ready')
        self.assertEqual(storage.recording('metadata-job')['source_metadata']['description'],
                         DESCRIPTION)
        storage.save_recording({'job_id': 'metadata-job', 'source': 'youtube'}, 'ready')
        self.assertEqual(storage.recording('metadata-job')['source_metadata']['description'],
                         DESCRIPTION)
