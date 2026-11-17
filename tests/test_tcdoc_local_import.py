"""本地图集导入的范围、预览、去重和异常回归；仅用隔离素材及虚构凭据。"""

import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from toolbox.tcdoc import import_local as imp


def schema():
    fields = []
    for number, semantic in enumerate(imp.ALIASES):
        kind = {"封面图": 5, "发布时间": 4, "作品链接": 8}.get(semantic, 1)
        raw = {"fieldID": f"f{number}", "fieldTitle": "链接" if semantic == "作品链接" else semantic,
               "fieldType": kind}
        if semantic in imp.CHOICE_COLUMNS:
            raw["fieldType"] = 17  # API 的 type=17 也可能是 isMultiple=False 的单选
            raw["propertySelect"] = {"isMultiple": False, "options": [
                {"id": "opt-" + semantic, "text": {"发布账号": "虚构账号", "发布平台": "微博"}.get(semantic, "动态"),
                 "style": 2}]}
        fields.append(raw)
    # 占位数字、服务端创建时间和额外列不能进入 values。
    fields.extend([{"fieldID": "distance", "fieldTitle": "发布距今（占位）", "fieldType": 2},
                   {"fieldID": "created", "fieldTitle": "创建时间", "fieldType": 12}])
    return fields


class FakeClient:
    doc_key, tab_id = imp.TEST_TARGET
    sheet_title = "虚构测试表"

    def __init__(self):
        self.fields, self.records, self.uploads, self.adds = schema(), [], [], []
        self.fail_add = False
        self.lose_body = False

    def get_fields(self):
        return copy.deepcopy(self.fields)

    def get_records(self):
        return copy.deepcopy(self.records)

    def upload_image(self, image):
        image.read_verified()
        self.uploads.append(image.path.name)
        return "fictional-image-" + str(len(self.uploads))

    def add_record(self, values):
        self.adds.append(copy.deepcopy(values))
        if self.fail_add:
            raise imp.UnknownWriteResult("虚构超时，新增结果不明")
        saved = copy.deepcopy(values)
        saved["封面图"] = [{"id": i["imageID"], "title": self.uploads[int(i["imageID"].split("-")[-1]) - 1]}
                           for i in saved["封面图"]]
        if self.lose_body:
            saved["作品内容"] = [{"type": "text", "text": "截断后的正文"}]
        rid = "fictional-row-" + str(len(self.records) + 1)
        self.records.append({"recordID": rid, "values": saved})
        return rid


class LocalImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fmjj-local-import-")
        self.root = Path(self.temp.name)
        self.media = self.root / "media"
        self.media.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def source(self, post_id="1234567890123456", title="测试标题", count=2, filename_title=None):
        directory = self.media / "虚构账号"
        directory.mkdir(exist_ok=True)
        stem = "20260823_虚构账号_" + imp.sanitize_filename(title if filename_title is None else filename_title, 50)
        for index in range(1, count + 1):
            name = stem + (".jpg" if count == 1 else f"_{index}.jpg")
            # 本地文件签名测试素材；不复制任何真实图集或凭据。
            (directory / name).write_bytes(b"\xff\xd8\xff" + bytes([index]) + b"fixture\xff\xd9")
        (directory / (stem + "_封面.jpg")).write_bytes(b"\xff\xd8\xffthumbnail")
        (directory / (stem + "_live.mov")).write_bytes(b"fictional-live-video")
        item = {"平台": "weibo", "视频ID": post_id, "标题": title,
                "正文": "第一行\n第二行&amp;结尾", "作者": "虚构账号", "发布时间": "2026-08-23 21:15",
                "链接": "https://m.weibo.cn/detail/" + post_id,
                "原始数据": {"pic_num": count, "pics": [{}] * count, "unused-secret": "do-not-export"}}
        export = self.root / (post_id + ".json")
        export.write_text(json.dumps([item], ensure_ascii=False), encoding="utf-8")
        return export

    def posts(self, *exports):
        return imp.load_posts(list(exports), self.media)

    def test_test_target_requires_exact_explicit_scope_before_auth(self):
        with self.assertRaises(imp.ImportErrorDetail):
            imp.validate_target(*imp.TEST_TARGET)
        imp.validate_target(*imp.TEST_TARGET, allow_test=True)
        with self.assertRaises(imp.ImportErrorDetail):
            imp.validate_target(imp.TEST_TARGET[0], "another-tab", allow_test=True)
        with patch.object(imp, "managed_cookie_reader", side_effect=AssertionError("must not read credentials")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(imp.main(["--doc-key", "unapproved", "--tab-id", "foreign", "--inspect"]), 2)

    def test_local_album_order_full_body_and_beijing_timestamp(self):
        post = self.posts(self.source(count=9))[0]
        self.assertEqual(len(post.images), 9)
        self.assertTrue(post.images[0].path.name.endswith("_1.jpg"))
        self.assertTrue(post.images[-1].path.name.endswith("_9.jpg"))
        self.assertEqual(post.body, "第一行\n第二行&结尾")
        self.assertEqual(post.columns()["发布时间"], "1787490900000")
        self.assertNotIn("do-not-export", repr(post))

    def test_filename_uses_original_export_title_when_entities_are_decoded(self):
        post = self.posts(self.source(title="鱼&amp;荷"))[0]
        self.assertEqual(post.title, "鱼&荷")
        self.assertIn("鱼&amp;荷", post.images[0].path.name)

    def test_missing_later_album_image_stops_the_whole_local_batch(self):
        first, later = self.source(), self.source("1234567890123457", "第二帖")
        (self.media / "虚构账号/20260823_虚构账号_第二帖_2.jpg").unlink()
        with self.assertRaises(imp.ImportErrorDetail):
            self.posts(first, later)

    def test_owned_collision_stem_is_read_without_rewriting_index(self):
        export = self.source(count=1)
        directory = self.media / "虚构账号"
        stem = "20260823_虚构账号_测试标题"
        original = directory / (stem + ".jpg")
        owned = directory / (stem + "~fixture.jpg")
        original.rename(owned)
        index = directory / ".stem_owners.json"
        index.write_text(json.dumps({stem: "other-post", stem + "~fixture": "1234567890123456"}), encoding="utf-8")
        before = index.read_bytes()
        self.assertEqual(self.posts(export)[0].images[0].path, owned)
        self.assertEqual(index.read_bytes(), before)

    def test_online_preview_never_uploads_and_reordered_fields_match_by_name(self):
        post = self.posts(self.source())[0]
        client = FakeClient()
        client.fields.reverse()
        events = []
        receipt = imp.run_import(client, [post], emit=events.append)
        self.assertEqual(client.uploads, [])
        self.assertEqual(client.adds, [])
        self.assertFalse(receipt["apply"])
        self.assertEqual(events[0]["records"][0]["columns"]["作品标题"], post.title)

    def test_missing_choice_in_later_post_fails_before_any_image_upload(self):
        first = self.posts(self.source())[0]
        later = imp.LocalPost("1234567890123457", "第二帖", "正文", "未知账号", first.published, first.images)
        client = FakeClient()
        with self.assertRaises(imp.ImportErrorDetail):
            imp.run_import(client, [first, later], apply=True)
        self.assertEqual(client.uploads, [])
        self.assertEqual(client.adds, [])

    def test_apply_verifies_all_values_and_rerun_skips_without_upload(self):
        post = self.posts(self.source())[0]
        client = FakeClient()
        receipt = imp.run_import(client, [post], apply=True)
        self.assertTrue(receipt["added"][0]["verified"])
        self.assertEqual(len(client.records), 1)
        self.assertEqual(receipt["uploaded_images"], 2)
        self.assertEqual(set(client.adds[0]), set(imp.ALIASES) - {"作品链接"} | {"链接"})
        self.assertEqual(client.adds[0]["发布账号"][0]["id"], "opt-发布账号")
        again = imp.run_import(client, [post], apply=True)
        self.assertEqual(len(again["skipped"]), 1)
        self.assertEqual(again["uploaded_images"], 0)
        self.assertEqual(again["added"], [])
        self.assertEqual(len(client.uploads), 2)

    def test_mobile_and_desktop_links_deduplicate_the_same_weibo_mid(self):
        # base62 1 + 2345 的数值；无需使用真实链接作测试样本。
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        number = sum(alphabet.index(c) * 62 ** i for i, c in enumerate(reversed("2345")))
        mid = str(10_000_000 + number)
        self.assertEqual(imp.weibo_key("https://m.weibo.cn/detail/" + mid + "?from=test#fragment"),
                         imp.weibo_key("https://weibo.com/12345/12345"))
        self.assertIsNone(imp.weibo_key("https://weibo.com.example.invalid/detail/" + mid))

    def test_uncertain_add_is_not_retried_and_receipt_identifies_the_source(self):
        post = self.posts(self.source())[0]
        client, events = FakeClient(), []
        client.fail_add = True
        with self.assertRaises(imp.UnknownWriteResult):
            imp.run_import(client, [post], apply=True, emit=events.append)
        self.assertEqual(len(client.adds), 1)
        self.assertTrue(events[-1]["unknown_record_outcome"])
        self.assertEqual(events[-1]["source_link"], post.link)
        self.assertNotIn("imageID", json.dumps(events))

    def test_readback_loss_stops_next_post_and_reports_created_id(self):
        first, later = self.posts(self.source(), self.source("1234567890123457", "第二帖"))
        client, events = FakeClient(), []
        client.lose_body = True
        with self.assertRaises(imp.ImportErrorDetail):
            imp.run_import(client, [first, later], apply=True, emit=events.append)
        self.assertEqual(len(client.adds), 1)
        self.assertEqual(events[-1]["added"][0]["record_id"], "fictional-row-1")
        self.assertFalse(events[-1]["added"][0]["verified"])

    def test_changed_image_after_preview_stops_before_upload(self):
        post = self.posts(self.source())[0]
        post.images[0].path.write_bytes(b"\xff\xd8\xffchanged")
        client = FakeClient()
        with self.assertRaises(imp.ImportErrorDetail):
            imp.run_import(client, [post], apply=True)
        self.assertEqual(client.uploads, [])

    def test_changed_field_after_preview_stops_before_upload(self):
        post = self.posts(self.source())[0]
        client = FakeClient()
        changed = copy.deepcopy(client.fields)
        changed[0]["fieldTitle"] = "被改名的标题"
        with patch.object(client, "get_fields", side_effect=[client.fields, changed]):
            with self.assertRaises(imp.ImportErrorDetail):
                imp.run_import(client, [post], apply=True)
        self.assertEqual(client.uploads, [])

    def test_multipart_sends_actual_local_bytes_and_preserves_chinese_filename(self):
        image = self.posts(self.source(count=1))[0].images[0]
        auth = imp.ManagedAuth(lambda: json.dumps({"client_id": "fictional-client", "access_token": "fictional-token",
                                                   "open_id": "fictional-account"}))
        client = imp.LocalImportClient(auth, *imp.TEST_TARGET, allow_test=True)
        response = io.StringIO(json.dumps({"ret": 0, "data": {"imageID": "fictional-image"}}))
        with patch.object(client.opener, "open", return_value=response) as request_call:
            self.assertEqual(client.upload_image(image), "fictional-image")
        request = request_call.call_args.args[0]
        self.assertEqual(request.full_url, imp.API_ROOT + "/resources/v2/images")
        self.assertTrue(request.get_header("Content-type").startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="image"', request.data)
        self.assertIn(image.path.name.encode("utf-8"), request.data)
        self.assertIn(image.read_verified(), request.data)
        self.assertNotIn(str(image.path).encode("utf-8"), request.data)

    def test_pagination_does_not_silently_drop_rows(self):
        auth = imp.ManagedAuth(lambda: json.dumps({"client_id": "fictional-client", "access_token": "fictional-token",
                                                   "open_id": "fictional-account"}))
        client = imp.LocalImportClient(auth, *imp.TEST_TARGET, allow_test=True)
        pages = [{"records": [{"recordID": "one"}], "total": 2, "hasMore": True, "next": 1},
                 {"records": [{"recordID": "two"}], "total": 2, "hasMore": False}]
        with patch.object(client, "_action", side_effect=pages):
            self.assertEqual(len(client.get_records()), 2)
        with patch.object(client, "_action", return_value={"records": [], "total": 1, "hasMore": False}):
            with self.assertRaises(imp.ImportErrorDetail):
                client.get_records()

    def test_account_change_and_service_errors_do_not_reveal_credentials(self):
        raw = json.dumps({"client_id": "fictional-client", "access_token": "fictional-token", "open_id": "fictional-account"})
        state = [raw]
        auth = imp.ManagedAuth(lambda: state[0])
        self.assertNotIn("fictional-token", repr(auth))
        state[0] = ""
        with self.assertRaises(imp.ImportErrorDetail):
            auth.headers()
        state[0] = raw
        client = imp.LocalImportClient(auth, *imp.TEST_TARGET, allow_test=True)
        response = io.StringIO(json.dumps({"ret": 123, "msg": "fictional-token", "data": {}}))
        with patch.object(client.opener, "open", return_value=response):
            with self.assertRaisesRegex(imp.ImportErrorDetail, "ret=123") as captured:
                client._request("GET", "/drive/v2/util/converter")
        self.assertNotIn("fictional-token", str(captured.exception))

    def test_cli_auth_read_and_write_guard_use_only_fictional_isolated_store(self):
        directory = self.root / "isolated-config"
        directory.mkdir()
        cookie_path = directory / "cookies.json"
        credential = json.dumps({"client_id": "fictional-client", "access_token": "fictional-token", "open_id": "fictional-account"})
        cookie_path.write_text(json.dumps({"cookie_enabled": True, "cookies": {imp.CRED_KEY: credential}}), encoding="utf-8")
        before = cookie_path.read_bytes()
        code = ("import sys; from pathlib import Path; "
                f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / 'src')!r}); "
                "from toolbox.tcdoc.import_local import install_readonly_guard, managed_cookie_reader, ManagedAuth; "
                "install_readonly_guard(); "
                "a=ManagedAuth(managed_cookie_reader(Path(sys.argv[1]))); "
                "assert a.headers()['Access-Token']=='fictional-token'; "
                "\ntry:\n Path(sys.argv[1], 'cookies.json').write_text('forbidden')"
                "\nexcept PermissionError:\n print('read-only verified')"
                "\nelse:\n raise AssertionError('write guard failed')")
        completed = subprocess.run([sys.executable, "-B", "-c", code, str(directory)],
                                   capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "read-only verified")
        self.assertEqual(cookie_path.read_bytes(), before)
        self.assertEqual(set(directory.iterdir()), {cookie_path})


if __name__ == "__main__":
    unittest.main()
