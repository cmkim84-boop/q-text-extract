"""앱 아이콘 생성기. 실행하면 assets/ 폴더에 icon.ico 와 크기별 PNG 를 만든다.
   python make_icon.py
"""
import os

from PIL import Image

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def build() -> Image.Image:
    """확정된 Q 아이콘 원본(assets/icon_master.png, 1024px)을 읽는다.
    (원본은 Poppins Bold 의 Q 글자를 바탕으로 디자인한 이미지라 윈도우 빌드 PC 에서 다시 그리지 않고 그대로 쓴다.)"""
    return Image.open(os.path.join(OUT, "icon_master.png")).convert("RGBA")


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    big = build()
    sizes = (16, 24, 32, 48, 64, 128, 256)
    for n in sizes:
        big.resize((n, n), Image.LANCZOS).save(os.path.join(OUT, f"icon_{n}.png"))
    big.resize((256, 256), Image.LANCZOS).save(
        os.path.join(OUT, "icon.ico"), sizes=[(n, n) for n in sizes]
    )
    big.resize((512, 512), Image.LANCZOS).save(os.path.join(OUT, "icon_512.png"))
    print("saved:", sorted(os.listdir(OUT)))
    write_embedded()


def write_embedded() -> None:
    """assets 폴더가 없어도 창/작업표시줄 아이콘이 나오도록 PNG 를 icon_data.py 에 심는다."""
    import base64

    lines = ['"""자동 생성 파일 (make_icon.py). 직접 고치지 마세요."""', "", "ICON_PNG_B64 = {"]
    for n in (16, 24, 32, 48, 64, 128, 256):
        with open(os.path.join(OUT, f"icon_{n}.png"), "rb") as f:
            lines.append(f'    {n}: "{base64.b64encode(f.read()).decode()}",')
    lines.append("}")
    with open(os.path.join(os.path.dirname(OUT), "icon_data.py"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("saved: icon_data.py")


if __name__ == "__main__":
    main()
