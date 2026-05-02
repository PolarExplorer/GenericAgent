import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory"))
from _common import *


def ocr_image(path):
    """图片OCR：使用 rapidocr"""
    from ocr_utils import ocr_image as _ocr

    result = _ocr(path)
    if isinstance(result, dict):
        return result.get("text", "")
    return str(result or "")


def extract_pdf_text(path):
    """PDF原生文字提取"""
    import pdfplumber

    texts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                texts.append(t)
    return "\n\n".join(texts)


def ocr_pdf(path):
    """PDF扫描件OCR"""
    from mineru_ocr import mineru_ocr as _ocr

    result = _ocr(path)
    if isinstance(result, dict):
        if result.get("success"):
            return result.get("md_text", "")
        raise RuntimeError(result.get("error") or "MinerU OCR 失败")
    return str(result or "")


def vision_describe(path):
    """使用视觉模型描述图片"""
    from vision_api import ask_vision

    return ask_vision(path)


def auto_strategy(path):
    """自动选择策略"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        text = extract_pdf_text(path)
        if text and len(text.strip()) > 50:
            info("策略: pdfplumber 原生提取")
            return text
        info("策略: mineru OCR (扫描件)")
        return ocr_pdf(path)

    info("策略: rapidocr 图片OCR")
    return ocr_image(path)


def run_ocr(args):
    if not os.path.exists(args.file):
        fail(f"文件不存在: {args.file}")
        return EXIT_FAIL

    try:
        if args.vision:
            result = vision_describe(args.file)
        elif args.force_ocr and args.file.lower().endswith(".pdf"):
            result = ocr_pdf(args.file)
        else:
            result = auto_strategy(args.file)
    except Exception as exc:
        fail(f"处理失败: {exc}")
        return EXIT_FAIL

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(result)
        ok(f"已保存到: {args.out}")
    else:
        print(result)
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(description="GA OCR 统一入口")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="OCR 图片或 PDF")
    p_run.add_argument("file", help="图片或PDF文件路径")
    p_run.add_argument("--force-ocr", action="store_true", help="强制OCR")
    p_run.add_argument("--vision", action="store_true", help="使用视觉模型")
    p_run.add_argument("--out", help="输出到文件")

    return parser


def main():
    parser = build_parser()

    argv = sys.argv[1:]
    if argv and argv[0] not in {"run", "-h", "--help"}:
        argv = ["run"] + argv

    args = parser.parse_args(argv)
    if args.command == "run":
        sys.exit(run_ocr(args))

    parser.print_help()
    sys.exit(EXIT_SKIP)


if __name__ == "__main__":
    main()
