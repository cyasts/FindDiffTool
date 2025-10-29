from google import genai
from google.genai import types
from PIL import Image
from io import BytesIO

def send_request(image_bytes: bytes, mask_bytes: bytes, prompt: str) -> bytes:
    http_options = types.HttpOptions(
        client_args={'proxy': 'socks5://127.0.0.1:7890'},       # Clash 通常 socks 是 7891；若你确实把 socks 配成 7890，就保留 7890
        async_client_args={'proxy': 'socks5://127.0.0.1:7890'},
    )
    client = genai.Client(
        vertexai=True,
        api_key="AQ.Ab8RN6J05lZBnxMUJZ6IdevFH3tmL_9MHn0R6lT8CLk3UDkFJQ",
        http_options=http_options
    )

    model = "gemini-2.5-flash-image"

    resp = client.models.generate_content(
        model=model,
        contents=[
            prompt,
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

if __name__ == "__main__":
    # 示例调用
    with open("dbg_input.png", "rb") as f:
        image_bytes = f.read()
    with open("dbg_mask.png", "rb") as f:
        mask_bytes = f.read()
    prompt = "在手上添加一把方向正确的手枪。"

    prop = (
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
            f"任务内容：{prompt}"
        )

    send_request(image_bytes, mask_bytes, prop)