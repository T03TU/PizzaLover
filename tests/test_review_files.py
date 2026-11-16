"""Review spreadsheet parsing and ingestion without Ollama or database writes."""
import asyncio
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from openpyxl import Workbook
from starlette.requests import Request

from api.server import ingest, preview_ingestion
from src.review_files import parse_review_file

HEADER = ['Feedback_ID', 'Franchise', 'Location', 'Rating', 'Comment']
ROW = ['FB-901', 'Pizza shop', 'Auckland', 5, 'Great pizza']


def xlsx(rows):
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class FileTests(unittest.TestCase):
    def test_csv_and_excel_match(self):
        csv = (','.join(HEADER) + '\n' + ','.join(map(str, ROW)) + '\n').encode()
        expected = dict(zip(HEADER, ROW))
        self.assertEqual(parse_review_file('reviews.xlsx', xlsx([HEADER, ROW])), [expected])
        csv_row = parse_review_file('reviews.CSV', csv)[0]
        self.assertEqual(csv_row['Comment'], expected['Comment'])

    def test_custom_columns_preview_and_mapping(self):
        content = b'id,shop,city,stars,review,unused\nFB-901,Pizza,Auckland,5,Great,ignore\n'
        preview = parse_review_file('reviews.csv', content, inspect=True)
        self.assertTrue(preview['requires_mapping'])
        self.assertEqual(preview['sample'][0]['review'], 'Great')
        mapping = dict(zip(HEADER, ['id', 'shop', 'city', 'stars', 'review']))
        rows = parse_review_file('reviews.csv', content, mapping)
        self.assertEqual(set(rows[0]), set(HEADER))
        self.assertEqual(rows[0]['Comment'], 'Great')
        self.assertEqual(parse_review_file('reviews.xlsx', xlsx([
            ['id', 'shop', 'city', 'stars', 'review', 'unused'],
            ['FB-901', 'Pizza', 'Auckland', 5, 'Great', 'ignore']]), mapping)[0]['Comment'], 'Great')
        for bad in [{}, dict(mapping, Comment='missing'), dict(mapping, Comment='stars')]:
            with self.assertRaises(ValueError):
                parse_review_file('reviews.csv', content, bad)
        with self.assertRaises(ValueError):
            parse_review_file('reviews.csv', b'id,id\na,b', inspect=True)

    def test_invalid_workbooks_and_headers(self):
        for name, content in [('reviews.xlsx', b'not excel'), ('reviews.xls', b''),
                              ('reviews.csv', b'Comment,Rating\nGreat,5'),
                              ('reviews.xlsx', xlsx([HEADER, ROW[:-1] + ['=1+1']]))]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse_review_file(name, content)
        with self.assertRaises(ValueError):
            parse_review_file('reviews.xlsx', xlsx([HEADER] + [ROW] * 501))


class IngestionTests(unittest.IsolatedAsyncioTestCase):
    async def submit(self, rows, mapping=None, preview=False):
        boundary = 'review-upload-test'
        content = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="reviews.xlsx"\r\n'
                   'Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n').encode()
        content += xlsx(rows)
        if mapping is not None:
            import json
            content += (f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="mapping"\r\n\r\n' + json.dumps(mapping)).encode()
        content += f'\r\n--{boundary}--\r\n'.encode()
        async def receive():
            return {'type': 'http.request', 'body': content, 'more_body': False}
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/ingest',
                           'headers': [(b'content-type', f'multipart/form-data; boundary={boundary}'.encode())],
                           'app': self.app}, receive)
        async def inline(function, *args, **kwargs):
            return function(*args, **kwargs)
        with patch('api.server.asyncio.to_thread', side_effect=inline):
            return await (preview_ingestion(request) if preview else ingest(request))

    async def test_mapped_upload_and_preview(self):
        stored = []
        store = SimpleNamespace(get=lambda **kwargs: {'metadatas': []},
                                add_documents=lambda docs, ids: stored.extend(docs))
        self.app = SimpleNamespace(state=SimpleNamespace(engine=SimpleNamespace(store=store), model_lock=asyncio.Lock()))
        columns = ['id', 'shop', 'city', 'stars', 'text', 'extra']
        rows = [columns, ROW + ['ignored']]
        preview = await self.submit(rows, preview=True)
        self.assertTrue(preview['requires_mapping'])
        self.assertEqual(stored, [])
        result = await self.submit(rows, mapping=dict(zip(HEADER, columns[:5])))
        self.assertEqual(result, {'added': 1})
        self.assertEqual(stored[0].page_content, 'Great pizza')
        self.assertEqual(set(stored[0].metadata), set(HEADER) - {'Comment'})

    async def test_embed_validate_and_duplicates(self):
        stored = []
        def add(docs, ids):
            stored.extend(docs)
            self.assertEqual(ids, ['FB-901'])
        store = SimpleNamespace(get=lambda **kwargs: {'metadatas': [d.metadata for d in stored]}, add_documents=add)
        self.app = SimpleNamespace(state=SimpleNamespace(engine=SimpleNamespace(store=store), model_lock=asyncio.Lock()))
        self.assertEqual(await self.submit([HEADER, ROW]), {'added': 1})
        self.assertEqual(stored[0].page_content, 'Great pizza')
        self.assertEqual(stored[0].metadata['Rating'], 5)
        with self.assertRaises(HTTPException) as duplicate:
            await self.submit([HEADER, ROW])
        self.assertEqual(duplicate.exception.status_code, 409)
        with self.assertRaises(HTTPException) as invalid:
            await self.submit([HEADER, ['FB-902', 'Pizza', 'NZ', 9, 'Invalid']])
        self.assertEqual(invalid.exception.status_code, 422)
        self.assertEqual(len(stored), 1)
