"""AI Video Director planning and project integration tests."""
import json
from io import BytesIO
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from reel_engine.director import (
    DirectorBrief, DirectorError, DirectorOrchestrator, FORMATS, OllamaProvider,
)
from reel_engine.project import open_project
from reel_engine.validation import load_timeline


class OllamaProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reel-ollama-test-")
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name) / "catalog-photo.png"
        self.image.write_bytes(b"image fixture")
        self.requests = []
        self.response = {
            "interpretation": {"summary": "A short travel film", "audience": "Travelers", "tone": "Warm"},
            "scenes": [{"narrative_title": "Opening", "narrative_purpose": "Introduce the topic",
                        "script": "A sample narration.", "media_id": "media_001",
                        "storyboard_title": "Opening shot", "storyboard_description": "Use the selected image."}],
        }
        self.chat_outputs = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                owner.requests.append(("GET", self.path, None))
                if self.path == "/redirect":
                    self.send_response(307)
                    self.send_header("Location", "http://example.com/collect")
                    self.end_headers()
                    return
                body = json.dumps({"models": [{"name": "test-local:latest"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(("POST", self.path, payload))
                if self.path == "/api/show":
                    body = json.dumps({"thinking": {"values": [False, True], "default": True}}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                elif owner.chat_outputs:
                    response_data = owner.chat_outputs.pop(0)
                else:
                    response_data = owner.response
                content = response_data if isinstance(response_data, str) else json.dumps(response_data)
                envelope = {"model": payload.get("model", "test-local:latest"), "created_at": "2026-11-15T00:00:00Z",
                            "message": {"role": "assistant", "content": content, "thinking": "simulated hidden trace"},
                            "done": True, "done_reason": "stop", "total_duration": 1000000,
                            "load_duration": 1000, "prompt_eval_count": 20, "eval_count": 8,
                            "eval_duration": 500000}
                body = json.dumps(envelope).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.provider = OllamaProvider("test-local:latest", base_url=f"http://127.0.0.1:{self.server.server_port}",
                                       timeout=2, diagnostic_dir=Path(self.temp.name) / "diagnostics")

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_local_server_lists_models_and_plan_maps_only_catalog_ids(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "http_proxy": "http://127.0.0.1:1", "NO_PROXY": ""}):
            models = self.provider.check_connection()
            self.assertEqual(models, ["test-local:latest"])
            brief = DirectorBrief("A short travel film", 10, "vertical", "Warm tone", (self.image,))
            plan = DirectorOrchestrator(self.provider).plan(brief)
        self.assertEqual(plan.provider_id, "ollama:test-local:latest")
        self.assertEqual(plan.timeline["scenes"][0]["asset"], str(self.image.resolve()))
        self.assertEqual(plan.scenes[0]["media_id"], "media_001")
        self.assertEqual(plan.phases["interpretation"]["audience"], "Travelers")
        self.assertEqual(plan.phases["script"][0]["text"], "A sample narration.")
        get = next(item for item in self.requests if item[0] == "GET")
        model_info = next(item for item in self.requests if item[1] == "/api/show")
        post = next(item for item in self.requests if item[1] == "/api/chat")
        self.assertEqual(get[:2], ("GET", "/api/tags"))
        self.assertEqual(model_info[:2], ("POST", "/api/show"))
        self.assertEqual(post[1], "/api/chat")
        user_payload = json.loads(post[2]["messages"][1]["content"])
        catalog = user_payload["available_media_catalog"]
        self.assertEqual(catalog, [{"media_id": "media_001", "filename": self.image.name,
                                    "type": "image", "duration_seconds": None}])
        self.assertNotIn(str(self.image.parent), json.dumps(user_payload))
        self.assertEqual(post[2]["model"], "test-local:latest")
        self.assertFalse(post[2]["stream"])
        self.assertIs(post[2]["think"], False)
        self.assertEqual(post[2]["options"]["temperature"], 0)
        self.assertEqual(post[2]["options"]["num_predict"], 4096)
        diagnostic=json.loads(self.provider.latest_diagnostic_path.read_text(encoding="utf-8"))
        attempt=diagnostic["attempts"][0]
        self.assertEqual(attempt["parse_status"], "valid")
        self.assertEqual(attempt["response_metadata"]["done_reason"], "stop")
        self.assertEqual(attempt["response_metadata"]["thinking_length"], len("simulated hidden trace"))
        self.assertNotIn("simulated hidden trace", json.dumps(diagnostic))
        self.assertNotIn(str(self.image.parent), json.dumps(diagnostic))
        self.assertFalse(diagnostic["privacy"]["request_prompt_saved"])

    def test_uninstalled_model_is_not_requested_or_downloaded(self):
        with self.assertRaisesRegex(DirectorError, "non è installato"):
            self.provider.__class__("missing-model", base_url=self.provider.base_url, timeout=2,
                                    diagnostic_dir=self.provider.diagnostic_dir).create_plan(
                DirectorBrief("Topic", 10, "vertical", "", (self.image,)),
                ({"media_id": "media_001", "path": self.image, "type": "image", "capacity": None},),
            )
        self.assertEqual([method for method, _path, _payload in self.requests], ["GET"])

    def test_model_cannot_invent_catalog_ids(self):
        self.response["scenes"][0]["media_id"] = "C:\\invented\\asset.png"
        brief = DirectorBrief("Topic", 10, "vertical", "", (self.image,))
        with self.assertRaisesRegex(DirectorError, "media_id non presente nel catalogo"):
            DirectorOrchestrator(self.provider).plan(brief)
        diagnostic=json.loads(self.provider.latest_diagnostic_path.read_text(encoding="utf-8"))
        self.assertEqual(len(diagnostic["attempts"]), 2)
        self.assertEqual(diagnostic["outcome"], "failed")

    def test_external_redirect_from_loopback_is_rejected(self):
        with self.assertRaisesRegex(DirectorError, "Redirect HTTP 307 rifiutato"):
            self.provider._request("/redirect", timeout=2)
        self.assertEqual([path for method, path, _payload in self.requests if method == "GET"], ["/redirect"])

    def test_valid_json_inside_fence_and_surrounding_text_is_normalized(self):
        self.chat_outputs=["Risposta JSON richiesta:\n```json\n"+json.dumps(self.response,ensure_ascii=False)+"\n```\nFine."]
        brief=DirectorBrief("Topic",10,"vertical","",(self.image,))
        plan=DirectorOrchestrator(self.provider).plan(brief)
        self.assertEqual(plan.scenes[0]["media_id"],"media_001")
        diagnostic=json.loads(self.provider.latest_diagnostic_path.read_text(encoding="utf-8"))
        self.assertEqual(len(diagnostic["attempts"]),1)
        self.assertEqual(diagnostic["attempts"][0]["parse_status"],"valid")

    def test_incomplete_json_gets_one_corrective_attempt(self):
        self.chat_outputs=['{"interpretation":{"summary":"incompleto"', self.response]
        brief=DirectorBrief("Topic",10,"vertical","",(self.image,))
        plan=DirectorOrchestrator(self.provider).plan(brief)
        self.assertEqual(len(plan.scenes),1)
        chats=[request for request in self.requests if request[1]=="/api/chat"]
        self.assertEqual(len(chats),2)
        self.assertIn("Correggi una volta",chats[1][2]["messages"][1]["content"])
        diagnostic=json.loads(self.provider.latest_diagnostic_path.read_text(encoding="utf-8"))
        self.assertEqual([item["parse_status"] for item in diagnostic["attempts"]],["invalid","valid"])
        self.assertEqual(diagnostic["outcome"],"success")

    def test_missing_fields_are_corrected_once_then_validated(self):
        missing={"interpretation":{"summary":"x","audience":"y","tone":"z"},"scenes":[{"media_id":"media_001"}]}
        self.chat_outputs=[missing,self.response]
        plan=DirectorOrchestrator(self.provider).plan(DirectorBrief("Topic",10,"vertical","",(self.image,)))
        self.assertEqual(plan.scenes[0]["script"],"A sample narration.")
        diagnostic=json.loads(self.provider.latest_diagnostic_path.read_text(encoding="utf-8"))
        self.assertIn("campi mancanti",diagnostic["attempts"][0]["parse_error"])
        self.assertEqual(len(diagnostic["attempts"]),2)

    def test_malformed_reply_after_single_correction_fails_and_is_persisted(self):
        self.chat_outputs=["non json", "{still not json"]
        with self.assertRaisesRegex(DirectorError,"dopo un tentativo di correzione"):
            DirectorOrchestrator(self.provider).plan(DirectorBrief("Test brief",10,"vertical","",(self.image,)))
        diagnostic=json.loads(self.provider.latest_diagnostic_path.read_text(encoding="utf-8"))
        self.assertEqual(diagnostic["outcome"],"failed")
        self.assertEqual(len(diagnostic["attempts"]),2)
        self.assertEqual(diagnostic["attempts"][1]["parse_status"],"invalid")
        serialized=json.dumps(diagnostic)
        self.assertNotIn("Test brief",serialized)
        self.assertFalse(diagnostic["privacy"]["request_prompt_saved"])

    def test_http_error_is_persisted_with_local_paths_redacted(self):
        url = self.provider.base_url + "/api/tags"
        failure = HTTPError(url, 500, "failure", {}, BytesIO(b"cannot read C:\\Users\\private\\model.gguf"))
        self.addCleanup(failure.close)
        with patch("reel_engine.director._OLLAMA_OPENER.open", side_effect=failure):
            with self.assertRaisesRegex(DirectorError, "HTTP 500"):
                DirectorOrchestrator(self.provider).plan(
                    DirectorBrief("No sensitive brief", 10, "vertical", "", (self.image,)))
        diagnostic=json.loads(self.provider.latest_diagnostic_path.read_text(encoding="utf-8"))
        serialized=json.dumps(diagnostic)
        self.assertIn("http_error", serialized)
        self.assertIn("<percorso locale redatto>", serialized)
        self.assertNotIn("C:\\Users\\private", serialized)
        self.assertEqual(diagnostic["outcome"],"failed")

    def test_timeout_has_clear_error_message(self):
        with patch("reel_engine.director._OLLAMA_OPENER.open", side_effect=TimeoutError("slow")) as request:
            with self.assertRaisesRegex(DirectorError, "Timeout Ollama dopo 2 s"):
                self.provider.check_connection()
            self.assertEqual(request.call_args.kwargs["timeout"], 2)

    def test_only_loopback_urls_are_accepted(self):
        with self.assertRaisesRegex(DirectorError, "solo su HTTP locale"):
            OllamaProvider("test", base_url="http://example.com:11434")
        with self.assertRaisesRegex(DirectorError, "solo su HTTP locale"):
            OllamaProvider("test", base_url="https://127.0.0.1:11434")


class DirectorWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reel-director-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image_a = self.root / "opening.png"
        self.image_b = self.root / "closing.jpg"
        self.image_a.write_bytes(b"synthetic image fixture")
        self.image_b.write_bytes(b"synthetic image fixture")
        self.orchestrator = DirectorOrchestrator()

    def brief(self, *, media=None, topic="Restauro Vespa", duration=20, video_format="vertical"):
        return DirectorBrief(topic, duration, video_format, "Tono documentaristico", tuple(media or (self.image_a, self.image_b)))

    def test_local_workflow_runs_all_stages_and_is_transparent_about_no_ai(self):
        plan = self.orchestrator.plan(self.brief())
        self.assertEqual(plan.provider_id, "local-template")
        self.assertIn("AI provider non configurato", plan.provider_status)
        self.assertEqual(set(plan.phases), {"interpretation", "narrative", "script", "storyboard", "asset_catalog", "asset_matching", "project_preparation"})
        self.assertEqual(len(plan.scenes), 2)
        self.assertEqual(sum(scene["duration"] for scene in plan.scenes), 20)
        self.assertEqual(plan.timeline["schema_version"], 1)
        self.assertEqual(plan.timeline["canvas"], FORMATS["vertical"])
        self.assertEqual(plan.phases["interpretation"]["summary"], "Restauro Vespa")
        self.assertEqual(set(plan.timeline["scenes"][0]), {"id", "type", "asset", "duration", "source_in"})

    def test_project_is_saved_as_editable_v1_and_storyboard_is_in_project_settings(self):
        plan = self.orchestrator.plan(self.brief(video_format="horizontal"))
        project = self.orchestrator.create_project(plan, "Direttore locale", self.root / "generated")
        reopened = open_project(project.path)
        timeline_path = self.root / "generated-timeline.json"
        timeline_path.write_text(json.dumps(reopened.timeline), encoding="utf-8")
        parsed = load_timeline(timeline_path)
        self.assertEqual(parsed.video.width, 1920)
        self.assertEqual(len(parsed.scenes), 2)
        self.assertEqual(reopened.timeline["schema_version"], 1)
        director_data = reopened.settings["ai_director"]
        self.assertEqual(director_data["provider"], "local-template")
        self.assertEqual(director_data["brief"]["topic"], "Restauro Vespa")
        self.assertEqual(len(director_data["phases"]["storyboard"]), 2)
        self.assertIn("testo guida da rivedere", director_data["phases"]["script"][0]["text"])

    def test_video_durations_are_capped_and_remainder_goes_to_stills(self):
        video = self.root / "short.mp4"
        video.write_bytes(b"mock video")
        with patch("reel_engine.director.probe_video_duration", return_value=2):
            plan = self.orchestrator.plan(self.brief(media=(video, self.image_a), duration=12))
        self.assertEqual(plan.scenes[0]["duration"], 2)
        self.assertEqual(plan.scenes[1]["duration"], 10)
        self.assertAlmostEqual(sum(scene["duration"] for scene in plan.scenes), 12)

    def test_video_only_plan_rejects_requested_duration_beyond_source_capacity(self):
        first, second = self.root / "first.mp4", self.root / "second.mov"
        first.write_bytes(b"video")
        second.write_bytes(b"video")
        with patch("reel_engine.director.probe_video_duration", side_effect=(3, 4)):
            with self.assertRaisesRegex(DirectorError, "durano in totale"):
                self.orchestrator.plan(self.brief(media=(first, second), duration=8))

    def test_missing_media_empty_topic_and_invalid_format_are_rejected(self):
        with self.assertRaisesRegex(DirectorError, "Inserisci l'argomento"):
            self.orchestrator.plan(self.brief(topic="  "))
        with self.assertRaisesRegex(DirectorError, "non trovato"):
            self.orchestrator.plan(self.brief(media=(self.root / "gone.png",)))
        with self.assertRaisesRegex(DirectorError, "Formato video"):
            self.orchestrator.plan(self.brief(video_format="cinema"))

    def test_malformed_provider_output_is_rejected_before_project_preparation(self):
        class BadProvider:
            provider_id = "bad-test-adapter"

            def create_plan(self, brief, media):
                return {"interpretation": {"summary": "x", "audience": "x", "tone": "x"},
                        "narrative": "not a list", "script": [], "storyboard": []}

        with self.assertRaisesRegex(DirectorError, "liste narrative"):
            DirectorOrchestrator(BadProvider()).plan(self.brief())

    def test_injected_provider_is_called_through_provider_neutral_contract(self):
        class StubProvider:
            provider_id = "test-adapter"

            def __init__(self):
                self.called = False

            def create_plan(self, brief, media):
                self.called = True
                count = len(media)
                narrative = [{"title": f"N{i}", "purpose": f"P{i}"} for i in range(count)]
                return {
                    "interpretation": {"summary": brief.topic, "audience": "Test", "tone": "Calm"},
                    "narrative": narrative,
                    "script": [{"text": "script"} for _ in media],
                    "storyboard": [{"title": f"N{i}", "description": "Shot", "media_id": media[i]["media_id"]} for i in range(count)],
                }

        provider = StubProvider()
        plan = DirectorOrchestrator(provider).plan(self.brief())
        self.assertTrue(provider.called)
        self.assertEqual(plan.provider_id, "test-adapter")


PYSIDE_AVAILABLE = __import__("importlib.util", fromlist=["find_spec"]).find_spec("PySide6") is not None
if PYSIDE_AVAILABLE:
    from PySide6.QtWidgets import QApplication
    from reel_engine.gui.director import AIVideoDirectorDialog


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 non installato nell'ambiente di test")
class DirectorDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["REEL ENGINE Director tests"])

    def test_gui_draft_runs_asynchronously_and_exposes_provider_choices(self):
        with tempfile.TemporaryDirectory(prefix="reel-director-async-") as temp:
            root = Path(temp)
            image = root / "scene.png"
            image.write_bytes(b"fixture")
            dialog = AIVideoDirectorDialog(projects_folder=root)
            self.addCleanup(dialog.close)
            dialog.topic_edit.setPlainText("Una breve storia")
            dialog.media_paths.append(image)
            dialog.media_list.addItem(image.name)
            self.assertEqual(dialog.provider_mode_combo.count(), 2)
            self.assertEqual(dialog.provider_mode_combo.itemData(0), "template")
            self.assertEqual(dialog.provider_mode_combo.itemData(1), "ollama")
            self.assertTrue(dialog.start_draft_generation())
            self.assertTrue(dialog._busy)
            for _ in range(100):
                self.app.processEvents()
                if not dialog._busy:
                    break
                self.app.processEvents()
                __import__("time").sleep(0.01)
            self.assertFalse(dialog._busy)
            self.assertIsNotNone(dialog.plan)
            self.assertTrue(dialog.create_button.isEnabled())
            self.assertIn("bozza locale deterministica", dialog.plan.provider_status)

    def test_dialog_previews_draft_then_creates_openable_project(self):
        with tempfile.TemporaryDirectory(prefix="reel-director-dialog-") as temp:
            root = Path(temp)
            image = root / "scene.png"
            image.write_bytes(b"fixture")
            dialog = AIVideoDirectorDialog(projects_folder=root)
            self.addCleanup(dialog.close)
            dialog.topic_edit.setPlainText("Viaggio in Sicilia")
            dialog.project_name_edit.setText("Sicilia")
            dialog.media_paths.append(image)
            dialog.media_list.addItem(image.name)
            self.assertTrue(dialog.generate_draft())
            self.assertTrue(dialog.create_button.isEnabled())
            self.assertIn("provider non configurato", dialog.preview_label.text())
            self.assertTrue(dialog.create_project())
            project_path = root / "Sicilia" / "project.json"
            self.assertTrue(project_path.is_file())
            self.assertEqual(open_project(project_path).timeline["scenes"][0]["asset"], str(image.resolve()))


if __name__ == "__main__":
    unittest.main()

