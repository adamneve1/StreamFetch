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

    def test_youtube_id_supports_known_urls_and_rejects_other_origins(self):
        for value in ['abcdefghijk', 'https://youtu.be/abcdefghijk?t=2',
                      'https://www.youtube.com/watch?v=abcdefghijk',
                      'https://youtube.com/shorts/abcdefghijk',
                      'https://youtube.com/live/abcdefghijk']:
            self.assertEqual(transcript_reader.youtube_id(value), 'abcdefghijk')
        for value in ['https://youtube.com.evil.example/watch?v=abcdefghijk',
                      'https://evil.example/abcdefghijk', 'javascript:abcdefghijk',
                      'https://user@youtube.com/watch?v=abcdefghijk', None, 'bad']:
            self.assertIsNone(transcript_reader.youtube_id(value))

    def test_persisted_id_embed_csp_and_non_youtube_compatibility(self):
        self.row['source_metadata']['youtube_id'] = 'abcdefghijk'
        storage.save_recording(self.row, 'ready')
        storage.save_transcription_state('reader-job', 'completed',
                                         txt_filename='reader.txt', srt_filename='reader.srt')
        self.assertEqual(self.data().json['youtube_id'], 'abcdefghijk')
        page = self.client.get('/api/recordings/reader-job/transcript/view')
        self.assertIn('frame-src https://www.youtube.com', page.headers['Content-Security-Policy'])
        self.assertNotIn('youtube.com', self.data().headers['Content-Security-Policy'])
        self.row['source'] = 'oryx'
        self.assertIsNone(transcript_reader.reader_data(self.row, self.root)['youtube_id'])

    def test_legacy_youtube_without_id_does_not_invent_a_player(self):
        self.assertIsNone(self.data().json['youtube_id'])

    def test_explicit_program_fields_and_guest_roles(self):
        parsed = transcript_reader.parse_source_metadata(DESCRIPTION)
        self.assertEqual(parsed['program'], 'Dialog Batam')
        self.assertEqual(parsed['date_time'], 'Minggu, 4 Oktober 2026 · 09.00 WIB')
        self.assertEqual(parsed['theme'], 'Pendidikan inklusif')
        self.assertEqual(parsed['presenter'], 'Sari\nSeluruh deskripsi asli.\nhttps://example.com')
        self.assertEqual([(guest['name'], guest['role']) for guest in parsed['guests']],
                         [('Dr. Rina', 'Dosen'), ('Budi', 'Kepala sekolah')])

    def test_reordered_rri_sections_secondary_fields_and_hashtag_footer(self):
        description = ('Dialog RRI Batam\n\nOperator Studio : Agus\n'
                       'Narasumber :\nDr. Rina - Dosen - Universitas Batam\nBudi\n'
                       'Jam : 09:00 WIB\nProduser : Ratna\n'
                       'Tema : Pendidikan\ninklusi: kesempatan untuk semua\n'
                       'Penanggung Jawab : Kepala RRI\n'
                       'Hari,Tanggal : Minggu, 4 Oktober 2026\nPresenter : Sari\n'
                       '#rri #dialog\nFooter tambahan')
        parsed = transcript_reader.parse_source_metadata(description)
        self.assertEqual(parsed['date_time'], 'Minggu, 4 Oktober 2026 · 09:00 WIB')
        self.assertEqual(parsed['theme'], 'Pendidikan\ninklusi: kesempatan untuk semua')
        self.assertEqual(parsed['guests'][0]['name'], 'Dr. Rina')
        self.assertEqual(parsed['guests'][0]['role'], 'Dosen - Universitas Batam')
        self.assertEqual(parsed['guests'][1]['role'], '')
        self.assertEqual(parsed['presenter'], 'Sari')
        info = transcript_reader.recording_info({'source_metadata': {
            'title': 'Judul YouTube', 'description': description}})
        self.assertEqual(info['program'], 'Judul YouTube')
        self.assertEqual(info['secondary'], {'producer': 'Ratna', 'studio_operator': 'Agus',
                                             'person_in_charge': 'Kepala RRI'})
        self.assertEqual(info['description'], description)

    def test_alias_case_and_whitespace_variations(self):
        parsed = transcript_reader.parse_source_metadata(
            '  pRoGrAm  ： Dialog\r\n hArI ,  TaNgGaL : Minggu\r\n'
            ' WaKtU : 09:00\n  ThEmE :  Pendidikan   inklusif\n'
            ' gUeSt : Rina - Dosen\n  HoSt : Sari\n'
            ' OPERATOR   STUDIO : Agus\n PENANGGUNG   JAWAB : Ratna')
        self.assertEqual(parsed['program'], 'Dialog')
        self.assertEqual(parsed['date_time'], 'Minggu · 09:00')
        self.assertEqual(parsed['theme'], 'Pendidikan inklusif')
        self.assertEqual(parsed['guests'][0]['role'], 'Dosen')
        self.assertEqual(parsed['presenter'], 'Sari')
        self.assertEqual(parsed['studio_operator'], 'Agus')
        self.assertEqual(parsed['person_in_charge'], 'Ratna')

    def test_multiline_values_unknown_sections_and_content_colons(self):
        raw = ('Pembukaan: bukan heading metadata\nTema:\nBaris satu\n\n'
               'Catatan: contoh nilai\nhttps://rri.co.id\n'
               'Editor :\nDina\nCatatan: tetap isi editor\n'
               'Penyiar: Sari #RRI\n#Batam')
        parsed = transcript_reader.parse_source_metadata(raw)
        self.assertEqual(parsed['theme'], 'Baris satu\nCatatan: contoh nilai\nhttps://rri.co.id')
        self.assertEqual(parsed['unknown_sections'], [
            {'heading': 'Editor', 'content': 'Dina\nCatatan: tetap isi editor'}])
        self.assertEqual(parsed['unparsed'], 'Pembukaan: bukan heading metadata')
        self.assertEqual(parsed['presenter'], 'Sari')
        self.assertEqual(parsed['description'], raw)

    def test_missing_sections_and_guest_name_hyphens_are_not_roles(self):
        parsed = transcript_reader.parse_source_metadata('Narasumber:\nAnne-Marie\nRina\n#RRI')
        self.assertEqual(parsed['date_time'], '')
        self.assertNotIn('theme', parsed)
        self.assertEqual([(guest['name'], guest['role']) for guest in parsed['guests']],
                         [('Anne-Marie', ''), ('Rina', '')])
        self.assertEqual(transcript_reader.recording_info({})['program'], 'Transcript')

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

    def test_local_media_url_for_supported_sources_and_missing_media(self):
        filename = 'Video Batam #1.mp4'
        self.root.joinpath(filename).write_bytes(b'0123456789')
        for source in ('tiktok', 'instagram', 'oryx', 'youtube'):
            row = dict(self.row, source=source, state='ready', filename=filename)
            data = transcript_reader.reader_data(row, self.root)
            self.assertEqual(data['media_url'], '/api/files/Video%20Batam%20%231.mp4?inline=1')
        for filename, state in [('missing.mp4', 'ready'), ('reader.txt', 'ready'), ('../secret.mp4', 'ready'),
                                ('Video Batam #1.mp4', 'failed')]:
            self.assertIsNone(transcript_reader.reader_data(dict(self.row, filename=filename, state=state), self.root)['media_url'])
        self.root.joinpath('symlink.mp4').symlink_to(self.root / 'Video Batam #1.mp4')
        self.assertIsNone(transcript_reader.reader_data(dict(self.row, filename='symlink.mp4', state='ready'), self.root)['media_url'])

    def test_existing_media_route_auth_inline_range_and_download_compatibility(self):
        self.root.joinpath('reader.mp4').write_bytes(b'0123456789')
        url = self.data().json['media_url']
        response = self.client.get(url, headers={'Range': 'bytes=2-5'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b'2345')
        self.assertEqual(response.headers['Content-Range'], 'bytes 2-5/10')
        self.assertEqual(response.mimetype, 'video/mp4')
        self.assertTrue(response.headers['Content-Disposition'].startswith('inline;'))
        self.assertEqual(self.client.get(url, headers={'Range': 'bytes=20-30'}).status_code, 416)
        self.assertTrue(self.client.get('/api/files/reader.mp4').headers['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(web.create_app(self.redis).test_client().get(url).status_code, 401)
        self.root.joinpath('reader.mp4').unlink()
        self.assertIsNone(self.data().json['media_url'])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_local_media_route_rejects_symlink_even_for_ready_recording(self):
        self.root.joinpath('reader.mp4').symlink_to(self.root / 'reader.txt')
        self.assertEqual(self.client.get('/api/files/reader.mp4?inline=1').status_code, 404)

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
