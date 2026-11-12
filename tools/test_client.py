"""Python 표준 라이브러리만 사용하는 대화형 출고 서버 테스트 도구."""

import argparse
import json
import os
from pathlib import Path
import shlex
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4


def load_api_key(env_file):
    """환경변수를 우선 사용하고, 없으면 .env의 API_KEY만 읽습니다."""
    if os.getenv('API_KEY'):
        return os.environ['API_KEY'], '환경변수 API_KEY'
    path = Path(env_file)
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line.startswith('export '):
                line = line[7:].lstrip()
            name, separator, value = line.partition('=')
            if separator and name.strip() == 'API_KEY':
                # Parse quotes/comments without executing shell commands or substitutions.
                tokens = shlex.split(value, comments=True, posix=True)
                if len(tokens) > 1:
                    raise ValueError('.env의 API_KEY에 공백이 있으면 따옴표로 감싸세요.')
                key = tokens[0] if tokens else ''
                return key, str(path) if key else '미설정'
    return '', '미설정'


def pretty(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def number(prompt, minimum=1, integer=True):
    while True:
        try:
            value = int(input(prompt)) if integer else float(input(prompt))
            if value < minimum or not float('-inf') < value < float('inf'):
                raise ValueError
            return value
        except ValueError:
            print(f'{minimum} 이상의 {"정수" if integer else "숫자"}를 입력하세요.')


def required(prompt):
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print('값을 입력하세요.')


class Client:
    def __init__(self, url, key):
        self.url = url.rstrip('/')
        self.key = key
        self.history = []

    def request(self, method, path, body=None):
        encoded = None if body is None else json.dumps(body).encode('utf-8')
        request = Request(self.url + path, data=encoded, method=method,
                          headers={'Content-Type': 'application/json', 'X-API-Key': self.key})
        try:
            with urlopen(request, timeout=10) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code == 401:
                raise RuntimeError(
                    'HTTP 401: API 키가 서버와 일치하지 않습니다. '
                    '서버와 동일한 .env를 사용하세요. 환경변수 API_KEY가 있으면 파일보다 우선합니다. '
                    '서버 키를 변경했다면 서버도 설정을 다시 불러와 재시작하세요.'
                ) from exc
            message = exc.read().decode('utf-8', errors='replace')
            raise RuntimeError(f'HTTP {exc.code}: {message}') from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f'서버 연결 실패: {exc}. 주소와 서버 실행 상태를 확인하세요.') from exc

    def submit(self, path, body):
        print('전송할 정보:')
        pretty(body)
        result = self.request('POST', path, body)
        pretty(result)
        for order in result.get('orders', [result]):
            if order['order_id'] not in [o['order_id'] for o in self.history]:
                self.history.append(order)

    def read_order(self, catalog):
        customer = required('주문자 ID: ')
        items = []
        ids = list(catalog)
        for index, key in enumerate(ids, 1):
            print(f'{index}. {catalog[key]["name"]} ({key})')
        while True:
            item = input('품목 번호 또는 ID (입력 완료: Enter): ').strip()
            if not item:
                if items:
                    break
                print('품목을 하나 이상 입력하세요.')
                continue
            if item.isdigit() and 1 <= int(item) <= len(ids):
                item = ids[int(item) - 1]
            if item not in catalog:
                print('등록되지 않은 품목입니다.')
                continue
            if item in [entry['item_id'] for entry in items]:
                print('이미 입력한 품목입니다.')
                continue
            items.append({'item_id': item, 'quantity': number('수량: ')})
        return {'request_id': str(uuid4()), 'customer_id': customer, 'items': items}

    def read_app_inventor_order(self, catalog):
        customer = required('주문자 ID: ')
        while len(customer) > 100:
            print('주문자 ID는 100자 이내로 입력하세요.')
            customer = required('주문자 ID: ')
        while True:
            row = [customer]
            print('A~D 수량을 입력하세요. 구매하지 않는 상품은 0을 입력하세요.')
            for index, product in enumerate('ABCD', 1):
                item_id = f'ITEM-00{index}'
                item = catalog.get(item_id)
                if item is None:
                    print(f'{product} ({item_id}): 서버에 미등록 — 수량 0으로 전송합니다. '
                          '서버의 품목 등록 설정을 확인하세요.')
                    quantity = 0
                else:
                    quantity = number(f'{product} / {item["name"]} ({item_id}) 수량: ', minimum=0)
                row.append({product: quantity})
            if any(entry[product] for product, entry in zip('ABCD', row[1:])):
                return row
            print('주문자마다 하나 이상의 상품 수량이 필요합니다. 다시 입력하세요.')

    def choose_order(self):
        result = self.request('GET', '/orders')
        while True:
            orders = result['orders']
            print(f'서버 저장 주문 {result["total"]}건 중 최근 {len(orders)}건')
            for index, order in enumerate(orders, 1):
                summary = ', '.join(f'{i["item_id"]} {i["quantity"]}개' for i in order['items'])
                print(f'{index}. {order["customer_id"]} / { {"waiting": "작업 시작 대기", "queued": "접수 대기", "running": "출고 처리 중", "waiting_box": "상자 교체 대기", "completed": "출고 완료", "failed": "처리 실패"}.get(order["status"], order["status"])} / {summary} / {order["order_id"]}')
            value = required('목록 번호 / 주문자 ID / 주문 ID (뒤로: b): ')
            if value.lower() == 'b':
                return None
            if value.isdigit() and 1 <= int(value) <= len(orders):
                return orders[int(value) - 1]['order_id']
            if value in [o['order_id'] for o in orders]:
                return value
            found = self.request('GET', '/orders?customer_id=' + quote(value, safe=''))
            if found['total'] == 1:
                return found['orders'][0]['order_id']
            if found['total'] > 1:
                print('이 주문자의 주문이 여러 건입니다. 목록에서 번호로 선택하세요.')
                result = found
                continue
            # Older order IDs might not be present in the latest page.
            try:
                self.get_order(value)
                return value
            except RuntimeError as exc:
                if 'HTTP 404:' not in str(exc):
                    raise
                print('해당 주문 또는 주문자를 찾을 수 없습니다. 목록 번호를 선택하세요.')

    def get_order(self, order_id):
        return self.request('GET', '/orders/' + quote(order_id, safe=''))

    def show_order(self, order, seen=None):
        states = {'waiting': '작업 시작 대기', 'queued': '접수 대기', 'running': '출고 처리 중',
                  'waiting_box': '상자 교체 대기',
                  'completed': '출고 완료', 'failed': '처리 실패'}
        if seen is None:
            print(f'주문자: {order["customer_id"]} / 상태: {states.get(order["status"], order["status"])}')
            for item in order['items']:
                print(f'  {item["item_id"]}: {item["quantity"]}개')
            seen = set()
        if 'progress' not in order:
            print('단계별 표시를 사용하려면 서버를 최신 코드로 재시작하세요.')
            return
        for event in order['progress']['events']:
            if event['id'] not in seen:
                print(event['message'])
                seen.add(event['id'])

    def watch(self):
        order_id = self.choose_order()
        if order_id is None:
            return
        print('1초 간격으로 조회합니다. Ctrl+C를 누르면 조회만 중지합니다.')
        seen = set()
        print('장치의 명령 전달 및 완료 기록을 표시합니다.')
        try:
            while True:
                order = self.get_order(order_id)
                self.show_order(order, seen)
                if order['status'] in ('completed', 'failed'):
                    break
                if order['status'] == 'waiting':
                    print('작업 시작 대기 중입니다. 관리자앱에서 이 주문을 선택하고 작업 시작을 누르세요.')
                    break
                if order['status'] == 'waiting_box':
                    print('상자 교체 대기 중입니다. 앱에서 준비 완료 또는 테스트 메뉴 8번으로 확인하세요.')
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            print('\n조회 중지. 서버의 주문 처리는 계속됩니다.')

    def run(self):
        print(f'테스트 서버: {self.url}')
        print('주문 전송은 연결된 서버에서 실제로 실행됩니다. 모의 테스트는 mode=simulation을 확인하세요.')
        while True:
            print('\n1 서버 상태  2 품목 목록  3 통합 주문 입력')
            print('5 주문 상세 조회  6 주문 완료까지 조회  7 접속 요청 기록')
            print('8 새 상자 준비 완료 (선택한 주문의 대기 해제)')
            print('0 종료')
            try:
                choice = input('선택: ').strip()
                if choice == '0':
                    return
                if choice == '1':
                    pretty(self.request('GET', '/health'))
                elif choice == '2':
                    pretty(self.request('GET', '/items'))
                elif choice == '3':
                    catalog = self.request('GET', '/items')
                    if not any(f'ITEM-00{i}' in catalog for i in range(1, 5)):
                        print('A~D에 해당하는 ITEM-001~ITEM-004가 없습니다. 서버의 품목 등록 설정을 확인하세요.')
                        continue
                    orders = []
                    while True:
                        print(f'주문자 {len(orders) + 1} 입력')
                        orders.append(self.read_app_inventor_order(catalog))
                        print(f'입력된 주문자: {len(orders)}명 (아직 전송하지 않았습니다.)')
                        while True:
                            action = input('다음 주문자 추가: Enter / 전체 전송: s / 취소: b: ').strip().lower()
                            if action in ('', 's', 'b'):
                                break
                            print('Enter, s, b 중 선택하세요.')
                        if action == 's':
                            self.submit('/orders/app-inventor',
                                        {'request_id': str(uuid4()), 'orders': orders})
                            break
                        if action == 'b':
                            print('주문 입력을 취소했습니다. 서버에 전송하지 않았습니다.')
                            break
                elif choice == '5':
                    order_id = self.choose_order()
                    if order_id is not None:
                        self.show_order(self.get_order(order_id))
                elif choice == '6':
                    self.watch()
                elif choice == '8':
                    order_id = self.choose_order()
                    if order_id and input('상자를 치우고 새 상자를 놓았습니까? 완료했다면 y: ').strip().lower() == 'y':
                        pretty(self.request('POST', '/orders/' + quote(order_id, safe='') + '/box-ready'))
                elif choice == '7':
                    records = self.request('GET', '/connections')['requests']
                    print('최근 접속 요청 (최대 200건, 최신순 / 시각 UTC)')
                    print('IP는 접속 기기 주소이며 사람의 신원이나 현재 연결 상태를 뜻하지 않습니다.')
                    for record in records:
                        print(f"{record['time']}  {record['ip']}  "
                              f"{record['method']} {record['path']}  HTTP {record['status']}")
                    if not records:
                        print('기록이 없습니다. 휴대폰에서 /health 접속 또는 주문 전송 후 다시 확인하세요.')
                else:
                    print('0, 1, 2, 3, 5, 6, 7, 8 중 선택하세요. 주문 입력은 3번입니다.')
            except (RuntimeError, ValueError) as exc:
                print(f'오류: {exc}')
            except (EOFError, KeyboardInterrupt):
                print('\n테스트 도구를 종료합니다. 서버의 주문 처리는 계속됩니다.')
                return


def main():
    parser = argparse.ArgumentParser(description='출고 서버 대화형 테스트 도구')
    parser.add_argument('--url', default='http://127.0.0.1:8000', help='서버 주소')
    parser.add_argument('--check', action='store_true', help='서버 상태만 조회하고 종료')
    parser.add_argument('--env-file', type=Path,
                        default=Path(__file__).resolve().parents[1] / '.env',
                        help='API_KEY를 읽을 설정 파일 (기본: 프로젝트 루트의 .env)')
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        parser.error('--url에 http:// 또는 https:// 서버 주소를 입력하세요.')
    try:
        key, source = load_api_key(args.env_file)
    except (OSError, ValueError):
        parser.exit(1, '설정 파일을 읽을 수 없습니다. 경로와 API_KEY의 따옴표 형식을 확인하세요.\n')
    print(f'API 키 설정 출처: {source} (키 값은 표시하지 않습니다.)')
    if not key:
        print('API 키가 없습니다. 서버에 키가 설정되어 있으면 .env에 같은 API_KEY를 입력하세요.')
    client = Client(args.url, key)
    if args.check:
        try:
            pretty(client.request('GET', '/health'))
        except RuntimeError as exc:
            parser.exit(1, f'{exc}\n')
    else:
        client.run()


if __name__ == '__main__':
    main()
