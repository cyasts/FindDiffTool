import os
import requests
import threading


class ImageEditRequester:
    def __init__(self, image_path: str, prompt: str):
        self.image_path = image_path
        self.prompt = prompt
        self.BASE_URL = "https://ai.t8star.cn/"
        # 建议在系统环境变量中设置 BANANA_API_KEY，避免把密钥写入代码库
        self.API_KEY = "sk-RX5FUdtuNTfQvr3LAOsDsL7OdkJZxf7DIhQ73Gfqj7yq50ZO"
        self.MODEL = os.environ.get("BANANA_MODEL", "nano-banana")
        self.url = f"{self.BASE_URL}/v1/images/edits"
        self.headers = {
            'Authorization': f'Bearer {self.API_KEY}'
        }

    def send_request(self):
        if not self.API_KEY:
            raise RuntimeError("缺少 BANANA_API_KEY 环境变量，无法调用AI接口")
        import base64
        from PIL import Image

        # 记录原始图片尺寸
        with Image.open(self.image_path) as img:
            width, height = img.size

        files = [
            ('image', (self.image_path, open(self.image_path, 'rb'), 'image/png')),
        ]
        payload = {
            'model': self.MODEL,
            'prompt': self.prompt,
            'response_format': 'b64_json',
            'size': f"{width}x{height}",
        }
        response = requests.request("POST", self.url, headers=self.headers, data=payload, files=files)

        try:
            resp_json = response.json()
        except Exception:
            raise RuntimeError(f"AI返回非JSON: {response.text[:200]}")

        if 'data' not in resp_json or not resp_json['data']:
            raise RuntimeError("AI返回数据为空")

        data0 = resp_json['data'][0]
        b64img = data0.get('b64_json')
        out_path = self.image_path.replace('.png', '_result.png')
        if b64img:
            # 兼容 data url 前缀
            if b64img.startswith('data:image'):
                b64img = b64img.split(',', 1)[-1]
            b64img = ''.join(b64img.split())
            # 修复base64 padding
            missing_padding = len(b64img) % 4
            if missing_padding:
                b64img += '=' * (4 - missing_padding)
            img_bytes = base64.b64decode(b64img)
            with open(out_path, 'wb') as f:
                f.write(img_bytes)
        elif 'url' in data0:
            img_url = data0['url']
            img_resp = requests.get(img_url)
            img_resp.raise_for_status()
            with open(out_path, 'wb') as f:
                f.write(img_resp.content)
        else:
            raise RuntimeError("AI未返回b64或url")

        # 保证输出尺寸一致
        with Image.open(out_path) as out_img:
            if out_img.size != (width, height):
                out_img = out_img.resize((width, height), Image.LANCZOS)
                out_img.save(out_path)
