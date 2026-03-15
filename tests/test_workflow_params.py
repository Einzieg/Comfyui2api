from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from comfyui2api.workflow_params import (
    detect_parameter_candidates,
    generate_parameter_template,
    load_workflow_parameter_spec,
    resolve_standard_overrides,
)


class WorkflowParameterMappingTests(unittest.TestCase):
    def test_sidecar_mapping_converts_size_fps_duration_and_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workflows_dir = root / "workflows"
            sidecar_dir = workflows_dir / ".comfyui2api"
            workflows_dir.mkdir(parents=True, exist_ok=True)
            sidecar_dir.mkdir(parents=True, exist_ok=True)

            workflow_path = workflows_dir / "video_flow.json"
            workflow_obj = {
                "prompt": {
                    "10": {
                        "class_type": "EmptyLatentImage",
                        "inputs": {"width": 512, "height": 512},
                        "_meta": {"title": "Latent Size"},
                    },
                    "11": {
                        "class_type": "KSampler",
                        "inputs": {"seed": 1},
                        "_meta": {"title": "Sampler"},
                    },
                    "20": {
                        "class_type": "VideoCombine",
                        "inputs": {"fps": 12, "frames": 48},
                        "_meta": {"title": "Video Output"},
                    },
                    "30": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "sample"}},
                }
            }
            workflow_path.write_text(json.dumps(workflow_obj, ensure_ascii=False), encoding="utf-8")

            sidecar = {
                "version": 1,
                "kind": "txt2video",
                "parameters": {
                    "size": {
                        "type": "size",
                        "maps": [
                            {"target": "10.width", "part": "width"},
                            {
                                "target": {
                                    "selector": {
                                        "class_type": "EmptyLatentImage",
                                        "title": "Latent Size",
                                        "input_key": "height",
                                    }
                                },
                                "part": "height",
                            },
                        ],
                    },
                    "fps": {
                        "type": "int",
                        "default": 12,
                        "maps": [
                            {
                                "target": {
                                    "selector": {
                                        "class_type": "VideoCombine",
                                        "title": "Video Output",
                                        "input_key": "fps",
                                    }
                                }
                            }
                        ],
                    },
                    "duration": {
                        "type": "float",
                        "maps": [
                            {"target": "20.frames", "transform": "seconds_to_frames", "fps_param": "fps", "round": "ceil"}
                        ],
                    },
                    "seed": {
                        "type": "int",
                        "maps": [{"target": "11.seed"}],
                    },
                },
            }
            (sidecar_dir / "video_flow.params.json").write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

            spec = load_workflow_parameter_spec(
                workflows_dir=workflows_dir,
                workflow_path=workflow_path,
                expected_kind="txt2video",
            )
            self.assertIsNotNone(spec)

            overrides = resolve_standard_overrides(
                workflow_obj=workflow_obj,
                spec=spec,
                request_params={"size": "1024x768", "fps": 24, "duration": 5, "seed": 7},
            )

            self.assertEqual(
                overrides,
                [
                    ("10", "width", 1024),
                    ("10", "height", 768),
                    ("11", "seed", 7),
                    ("20", "fps", 24),
                    ("20", "frames", 120),
                ],
            )

            detected = detect_parameter_candidates(workflow_obj)
            self.assertEqual(detected["size"][0]["maps"][0]["ref"], "10.width")
            self.assertEqual(detected["size"][0]["maps"][1]["ref"], "10.height")
            self.assertEqual(detected["fps"][0]["maps"][0]["ref"], "20.fps")
            self.assertEqual(detected["duration"][0]["maps"][0]["ref"], "20.frames")
            self.assertEqual(detected["duration"][0]["maps"][0]["transform"], "seconds_to_frames")
            self.assertEqual(detected["duration"][0]["paired_fps_ref"], "20.fps")

            template = generate_parameter_template(workflow_obj=workflow_obj, kind="txt2video", spec=spec)
            self.assertEqual(template["parameters"]["size"]["maps"][0]["target"]["ref"], "10.width")
            self.assertEqual(template["parameters"]["fps"]["default"], 12)
            self.assertEqual(template["parameters"]["duration"]["maps"][0]["transform"], "seconds_to_frames")
            self.assertEqual(template["parameters"]["duration"]["maps"][0]["fps_param"], "fps")


if __name__ == "__main__":
    unittest.main()
