import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.test_client import Client, load_api_key


class ClientConfigTests(unittest.TestCase):
    def test_dotenv_quotes_and_comments(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / '.env'
            for text, expected in [
                ('API_KEY=example-key # comment', 'example-key'),
                ('export API_KEY="key with spaces"', 'key with spaces'),
                ("API_KEY='key#literal'", 'key#literal'),
                ('API_KEY=$(not-executed)', '$(not-executed)'),
            ]:
                path.write_text(text)
                self.assertEqual(load_api_key(path), (expected, str(path)))

    def test_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('API_KEY=file-key')
            with patch.dict(os.environ, {'API_KEY': 'environment-key'}):
                self.assertEqual(load_api_key(path)[0], 'environment-key')

    def test_empty_environment_falls_back_to_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'API_KEY': ''}):
            path = Path(directory) / '.env'
            path.write_text('API_KEY=file-key')
            self.assertEqual(load_api_key(path)[0], 'file-key')

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_api_key(Path(directory) / '.env'), ('', '미설정'))


class OrderSelectionTests(unittest.TestCase):
    def test_customer_with_multiple_orders_requires_selection(self):
        orders = [{'order_id': str(i), 'customer_id': 'person-1',
                   'status': 'completed', 'items': []} for i in [10, 20]]
        client = Client('http://localhost', '')
        result = {'total': 2, 'orders': orders}
        with patch.object(client, 'request', return_value=result), \
             patch('builtins.input', side_effect=['person-1', '2']), patch('builtins.print'):
            self.assertEqual(client.choose_order(), '20')

    def test_saved_customer_and_cancel(self):
        client = Client('http://localhost', '')
        saved = {'total': 1, 'orders': [{'order_id': 'old', 'customer_id': 'person-1',
                                        'status': 'completed', 'items': []}]}
        with patch.object(client, 'request', return_value=saved), \
             patch('builtins.input', return_value='person-1'), patch('builtins.print'):
            self.assertEqual(client.choose_order(), 'old')
        with patch.object(client, 'request', return_value=saved), \
             patch('builtins.input', return_value='b'), patch('builtins.print'):
            self.assertIsNone(client.choose_order())


if __name__ == '__main__':
    unittest.main()


class FriendlyOrderTests(unittest.TestCase):
    def test_app_inventor_unified_menu(self):
        from app.main import AppInventorBatch

        catalog = {f'ITEM-00{i}': {'name': p} for i, p in enumerate('ABCD', 1)}
        for count in (1, 2):
            client = Client('http://localhost', '')
            inputs = ['3']
            inputs += ['person-1', '2', '0', '0', '1']
            rows = [['person-1', {'A': 2}, {'B': 0}, {'C': 0}, {'D': 1}]]
            if count == 2:
                inputs += ['', 'person-2', '0', '1', '3', '0']
                rows.append(['person-2', {'A': 0}, {'B': 1}, {'C': 3}, {'D': 0}])
            inputs += ['s', '0']
            response = {'orders': [{'order_id': str(i), 'customer_id': row[0],
                                    'status': 'queued'} for i, row in enumerate(rows)]}
            with patch.object(client, 'request', side_effect=[catalog, response]) as request, \
                 patch('builtins.input', side_effect=inputs), patch('builtins.print'):
                client.run()
            self.assertEqual(request.call_count, 2)
            first = request.call_args_list[1]
            self.assertEqual(first.args[:2], ('POST', '/orders/app-inventor'))
            self.assertEqual(first.args[2]['orders'], rows)
            AppInventorBatch.model_validate(first.args[2])
            self.assertEqual(len(client.history), len(rows))

    def test_app_inventor_rejects_invalid_and_all_zero_quantities(self):
        client = Client('http://localhost', '')
        catalog = {f'ITEM-00{i}': {'name': p} for i, p in enumerate('ABCD', 1)}
        with patch('builtins.input', side_effect=[
                'person-1', '-1', '1.5', 'abc', '0', '0', '0', '0',
                '0', '0', '0', '2']), patch('builtins.print'):
            row = client.read_app_inventor_order(catalog)
        self.assertEqual(row, ['person-1', {'A': 0}, {'B': 0}, {'C': 0}, {'D': 2}])

    def test_unified_order_cancel_does_not_submit(self):
        client = Client('http://localhost', '')
        catalog = {f'ITEM-00{i}': {'name': p} for i, p in enumerate('ABCD', 1)}
        with patch.object(client, 'request', return_value=catalog) as request, \
             patch('builtins.input', side_effect=['3', 'person-1', '1', '0', '0', '0',
                                                  'invalid', 'b', '0']), \
             patch('builtins.print'):
            client.run()
        request.assert_called_once_with('GET', '/items')

    def test_app_inventor_missing_d_is_reported_and_sent_as_zero(self):
        client = Client('http://localhost', '')
        catalog = {f'ITEM-00{i}': {'name': p} for i, p in enumerate('ABC', 1)}
        with patch('builtins.input', side_effect=['person-1', '1', '0', '0']), \
             patch('builtins.print') as output:
            row = client.read_app_inventor_order(catalog)
        self.assertEqual(row[-1], {'D': 0})
        self.assertTrue(any('미등록' in str(call) for call in output.call_args_list))

    def test_number_selection_uses_catalog_order(self):
        client = Client('http://localhost', '')
        with patch('builtins.input', side_effect=['person-1', '2', '3', '']), patch('builtins.print'):
            order = client.read_order({'X': {'name': '첫 품목'}, 'NEW': {'name': '새 품목'}})
        self.assertEqual(order['items'], [{'item_id': 'NEW', 'quantity': 3}])

    def test_progress_is_printed_once(self):
        client = Client('http://localhost', '')
        order = {'customer_id': 'person-1', 'status': 'completed', 'items': [],
                 'progress': {'events': [{'id': 'event-1', 'message': '[모의 실행] 로봇팔로 물건을 옮겼습니다.'}]}}
        seen = set()
        with patch('builtins.print') as output:
            client.show_order(order, seen)
            client.show_order(order, seen)
        self.assertEqual(output.call_count, 1)

    def test_watch_waiting_order_explains_admin_start_and_returns(self):
        client = Client('http://localhost', '')
        order = {'customer_id': 'person-1', 'status': 'waiting', 'items': [],
                 'progress': {'events': []}}
        with patch.object(client, 'choose_order', return_value='waiting-order'), \
             patch.object(client, 'get_order', return_value=order) as get_order, \
             patch('tools.test_client.time.sleep') as sleep, patch('builtins.print') as output:
            client.watch()
        get_order.assert_called_once_with('waiting-order')
        sleep.assert_not_called()
        self.assertTrue(any('관리자앱' in str(call) for call in output.call_args_list))
