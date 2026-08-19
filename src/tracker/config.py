from pathlib import Path

BILIBILI_MID = "1467772"
DOUYIN_SEC_UID = "MS4wLjABAAAA0Wk4gxp3AYFnqoqo-IBF6lbdLnrxgjy__DdhPBNBkws"
WEIBO_UID = "3056354293"

OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_PATHS = {
    "bilibili": OUTPUT_DIR / "bilibili",
    "weibo":    OUTPUT_DIR / "weibo",
    "douyin":   OUTPUT_DIR / "douyin",
}

REQUEST_TIMEOUT = 20
PLAYWRIGHT_TIMEOUT = 35000

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; SM-S9080) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)
