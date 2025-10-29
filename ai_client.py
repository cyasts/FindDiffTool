import requests, base64, io
from models import CANVAS_W, CANVAS_H
from google import genai
from google.genai import types

from secret_key import A8_key, GEMINI_key

def generate_prompt(prop) -> str:
    return (
        "任务设定：我从一张大图裁出 ROI 放到固定画布中央；"
        "画布的非编辑区已是纯白(255,255,255,255)。\n"
        "遮罩规范（务必遵守）：\n"
        "• mask 仅用作选择：透明像素=允许编辑；不透明像素=禁止编辑；不得对 mask 本身做任何绘制或改动。\n"
        "• 只在『image』图像中与 mask 透明区域对应的像素内进行修改；"
        "严禁改变位置/大小/边界对齐；禁止外扩或羽化边缘。\n"
        "• 非编辑区必须与输入像素完全一致（保持纯白 255,255,255,255），"
        "不得新增阴影/纹理/噪声/描边。\n"
        "• 保持几何与透视不变，仅做必要内容替换或修饰，尽量保持原有结构线条。\n"
        "输出要求：返回整张 PNG；非编辑区需为不透明白色(Alpha=255)；禁止透明背景与棋盘格效果。\n"
        f"任务内容：{prop}"
    )

class A81ImageEditClient:
    def __init__(self, api: str):
        if (api == "A81"):
            self.BASE_URL = "https://ai.t8star.cn/"
        elif (api == "HK"):
            self.BASE_URL = "https://hk-api.gptbest.vip"
        elif (api == "US"):
            self.BASE_URL = "https://api.gptbest.vip"
        elif (api == "A82"):
            self.BASE_URL = "http://104.194.8.112:9088"
        self.headers = {
            'Authorization': f'Bearer {A8_key}'
        }
        self.url = f"{self.BASE_URL}/v1/images/edits"
        self.MODEL = "nano-banana"

    def send_request(self, image_bytes: bytes, mask_bytes: bytes, prompt: str) -> bytes:
        # 记录原始图片尺寸
        files = [
            ('image', ('input.png', io.BytesIO(image_bytes), 'image/png')),
            ('mask', ('mask.png', io.BytesIO(mask_bytes), "image/png")),
        ]
        payload = {
            'model': self.MODEL,
            'prompt': generate_prompt(prompt),
            'response_format': 'b64_json',
            # 'aspect_ratio': '4:3',
            'size': f"{CANVAS_W}x{CANVAS_H}",
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

        elif 'url' in data0:
            img_url = data0['url']
            img_resp = requests.get(img_url)
            img_bytes = img_resp.content
        else:
            raise RuntimeError("AI未返回b64或url")

        return img_bytes

http_options = types.HttpOptions(
    client_args={'proxy': 'socks5://127.0.0.1:7890'},       # Clash 通常 socks 是 7891；若你确实把 socks 配成 7890，就保留 7890
    async_client_args={'proxy': 'socks5://127.0.0.1:7890'},
)

class GeminiImageEditClient:
    def __init__(self):
        self.model = "gemini-2.5-flash-image"
        self.client = genai.Client(
            vertexai=True,
            api_key=GEMINI_key,
            http_options=http_options
        )

    def send_request(self, image_bytes: bytes, mask_bytes: bytes, prompt: str) -> bytes:
        resp = self.client.models.generate_content(
            model=self.model,
            contents=[
                generate_prompt(prompt),
                types.Part.from_bytes(data=image_bytes, mime_type='image/png'),
                types.Part.from_bytes(data=mask_bytes, mime_type='image/png'),
            ]
        )

        for part in resp.candidates[0].content.parts:
            if part.text is not None:
                print(part.text)
            elif part.inline_data is not None:
                return part.inline_data.data
        return ""