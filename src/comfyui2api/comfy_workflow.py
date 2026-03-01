from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


class WorkflowFormatError(ValueError):
    pass


def read_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8-sig")
    return json.loads(text)


def looks_like_prompt_graph(obj: Any) -> bool:
    if not isinstance(obj, dict) or not obj:
        return False
    hits = 0
    for k, v in list(obj.items())[:10]:
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        if "class_type" in v:
            hits += 1
    return hits > 0


def extract_prompt_and_extra(obj: Any) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    if isinstance(obj, dict) and isinstance(obj.get("prompt"), dict):
        prompt = obj["prompt"]
        extra_data: Optional[Dict[str, Any]] = None
        if isinstance(obj.get("extra_data"), dict):
            extra_data = obj["extra_data"]
        elif isinstance(obj.get("extra_pnginfo"), dict):
            extra_data = {"extra_pnginfo": obj["extra_pnginfo"]}
        elif isinstance(obj.get("workflow"), dict):
            extra_data = {"extra_pnginfo": {"workflow": obj["workflow"]}}
        return prompt, extra_data

    if looks_like_prompt_graph(obj):
        return obj, None

    if isinstance(obj, dict) and ("nodes" in obj or "links" in obj):
        raise WorkflowFormatError(
            "UI workflow JSON detected (contains 'nodes'/'links'). Export 'API format' from ComfyUI and retry."
        )

    raise WorkflowFormatError("Unrecognized workflow JSON format. Expected API prompt format.")


def parse_node_input_ref(raw: str, *, default_input: str = "text") -> Tuple[str, str]:
    s = str(raw or "").strip()
    if not s:
        raise ValueError("Empty node reference.")
    if "." in s:
        node_id, input_name = s.split(".", 1)
        node_id = node_id.strip()
        input_name = input_name.strip()
        if not node_id or not input_name:
            raise ValueError(f"Invalid node reference: {raw!r}")
        return node_id, input_name
    return s, default_input


def as_str(v: Any) -> str:
    return v if isinstance(v, str) else str(v or "")


def get_node_title(node: Dict[str, Any]) -> str:
    meta = node.get("_meta")
    if isinstance(meta, dict):
        title = meta.get("title")
        if isinstance(title, str):
            return title
    return ""


def find_text_prompt_targets(
    prompt: Dict[str, Any],
) -> Tuple[List[Tuple[str, str, str, str]], List[Tuple[str, str, str, str]]]:
    pos: List[Tuple[str, str, str, str]] = []
    neg: List[Tuple[str, str, str, str]] = []

    def add(kind: str, node_id: str, input_key: str, cls: str, title: str) -> None:
        item = (node_id, input_key, cls, title)
        if kind == "pos":
            if item not in pos:
                pos.append(item)
        else:
            if item not in neg:
                neg.append(item)

    for node_id, node in prompt.items():
        if not isinstance(node_id, str) or not isinstance(node, dict):
            continue
        cls = as_str(node.get("class_type"))
        title = get_node_title(node)
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        string_inputs = [k for k, v in inputs.items() if isinstance(k, str) and isinstance(v, str)]
        if not string_inputs:
            continue

        title_l = title.lower()
        cls_l = cls.lower()
        is_encode = "textencode" in cls_l

        preferred_key = "text" if "text" in inputs and isinstance(inputs.get("text"), str) else string_inputs[0]

        if "negative" in title_l or "neg" in title_l:
            add("neg", node_id, preferred_key, cls, title)
            continue
        if "positive" in title_l or "pos" in title_l:
            add("pos", node_id, preferred_key, cls, title)
            continue

        if is_encode:
            add("pos", node_id, preferred_key, cls, title)
            add("neg", node_id, preferred_key, cls, title)

    for _, node in prompt.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key in ("positive", "negative"):
            v = inputs.get(key)
            if not (isinstance(v, list) and len(v) >= 1 and isinstance(v[0], str)):
                continue
            ref_id = v[0]
            ref = prompt.get(ref_id)
            if not isinstance(ref, dict):
                continue
            ref_inputs = ref.get("inputs")
            if not isinstance(ref_inputs, dict):
                continue
            if "text" in ref_inputs and isinstance(ref_inputs.get("text"), str):
                cls = as_str(ref.get("class_type"))
                title = get_node_title(ref)
                if key == "positive":
                    add("pos", ref_id, "text", cls, title)
                else:
                    add("neg", ref_id, "text", cls, title)

    return pos, neg


def find_load_image_targets(prompt: Dict[str, Any]) -> List[Tuple[str, str, str, str]]:
    candidates: List[Tuple[str, str, str, str]] = []
    for node_id, node in prompt.items():
        if not isinstance(node_id, str) or not isinstance(node, dict):
            continue
        cls = as_str(node.get("class_type"))
        if "loadimage" not in cls.lower():
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if isinstance(inputs.get("image"), str):
            candidates.append((node_id, "image", cls, get_node_title(node)))
            continue
        string_inputs = [k for k, v in inputs.items() if isinstance(k, str) and isinstance(v, str)]
        if not string_inputs:
            continue
        preferred_key = "image" if "image" in string_inputs else string_inputs[0]
        candidates.append((node_id, preferred_key, cls, get_node_title(node)))
    return candidates


def pick_unique_target(*, kind: str, candidates: List[Tuple[str, str, str, str]]) -> Tuple[str, str]:
    if not candidates:
        raise KeyError(f"No {kind} prompt text node found in workflow.")

    scored: List[Tuple[int, Tuple[str, str, str, str]]] = []
    for c in candidates:
        node_id, input_key, cls, title = c
        score = 0
        title_l = title.lower()
        cls_l = cls.lower()
        if input_key == "text":
            score += 10
        if "textencode" in cls_l:
            score += 5
        if kind == "positive":
            if "positive" in title_l or "pos" in title_l:
                score += 100
        else:
            if "negative" in title_l or "neg" in title_l:
                score += 100
        scored.append((score, c))

    scored.sort(key=lambda x: (x[0], x[1][0]), reverse=True)
    best_score = scored[0][0]
    best = [c for s, c in scored if s == best_score]
    if len(best) == 1:
        node_id, input_key, _, _ = best[0]
        return node_id, input_key

    lines = [f"Ambiguous {kind} prompt node. Candidates (same score={best_score}):"]
    for node_id, input_key, cls, title in best[:12]:
        t = title if title else "(no title)"
        lines.append(f"  - {node_id}.{input_key}  class={cls}  title={t}")
    raise KeyError("\n".join(lines))


def pick_unique_load_image_target(candidates: List[Tuple[str, str, str, str]]) -> Tuple[str, str]:
    if not candidates:
        raise KeyError("No LoadImage node found in workflow.")
    if len(candidates) == 1:
        node_id, input_key, _, _ = candidates[0]
        return node_id, input_key
    scored: List[Tuple[int, Tuple[str, str, str, str]]] = []
    for c in candidates:
        node_id, input_key, cls, title = c
        score = 0
        title_l = title.lower()
        if "load" in title_l:
            score += 10
        if input_key == "image":
            score += 5
        scored.append((score, c))
    scored.sort(key=lambda x: (x[0], x[1][0]), reverse=True)
    best_score = scored[0][0]
    best = [c for s, c in scored if s == best_score]
    if len(best) == 1:
        node_id, input_key, _, _ = best[0]
        return node_id, input_key
    lines = [f"Ambiguous image node. Candidates (same score={best_score}):"]
    for node_id, input_key, cls, title in best[:12]:
        t = title if title else "(no title)"
        lines.append(f"  - {node_id}.{input_key}  class={cls}  title={t}")
    raise KeyError("\n".join(lines))


def apply_overrides(prompt: Dict[str, Any], overrides: List[Tuple[str, str, Any]]) -> None:
    for node_id, input_key, value in overrides:
        node = prompt.get(node_id)
        if not isinstance(node, dict):
            raise KeyError(f"Node {node_id!r} not found in workflow prompt graph.")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            node["inputs"] = {}
            inputs = node["inputs"]
        inputs[str(input_key)] = value


def prepare_prompt(
    *,
    workflow_obj: Any,
    positive_prompt: Optional[str],
    negative_prompt: Optional[str],
    positive_prompt_node: Optional[str],
    negative_prompt_node: Optional[str],
    image: Optional[str],
    image_node: Optional[str],
    overrides: List[Tuple[str, str, Any]],
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], List[Tuple[str, str, Any]]]:
    prompt, extra_data = extract_prompt_and_extra(workflow_obj)

    prompt_overrides: List[Tuple[str, str, Any]] = []
    need_auto = False
    if positive_prompt and not (positive_prompt_node or "").strip():
        need_auto = True
    if negative_prompt and not (negative_prompt_node or "").strip():
        need_auto = True
    if image and not (image_node or "").strip():
        need_auto = True

    pos_candidates: List[Tuple[str, str, str, str]] = []
    neg_candidates: List[Tuple[str, str, str, str]] = []
    img_candidates: List[Tuple[str, str, str, str]] = []
    if need_auto:
        pos_candidates, neg_candidates = find_text_prompt_targets(prompt)
        img_candidates = find_load_image_targets(prompt)

    if positive_prompt:
        node_ref = (positive_prompt_node or "").strip()
        if node_ref:
            node_id, input_key = parse_node_input_ref(node_ref, default_input="text")
        else:
            node_id, input_key = pick_unique_target(kind="positive", candidates=pos_candidates)
        prompt_overrides.append((node_id, input_key, str(positive_prompt)))

    if negative_prompt:
        node_ref = (negative_prompt_node or "").strip()
        if node_ref:
            node_id, input_key = parse_node_input_ref(node_ref, default_input="text")
        else:
            node_id, input_key = pick_unique_target(kind="negative", candidates=neg_candidates)
        prompt_overrides.append((node_id, input_key, str(negative_prompt)))

    if image:
        node_ref = (image_node or "").strip()
        if node_ref:
            node_id, input_key = parse_node_input_ref(node_ref, default_input="image")
        else:
            node_id, input_key = pick_unique_load_image_target(img_candidates)
        prompt_overrides.append((node_id, input_key, str(image)))

    combined_overrides = prompt_overrides + list(overrides or [])
    if combined_overrides:
        apply_overrides(prompt, combined_overrides)
    return prompt, extra_data, combined_overrides


def iter_file_outputs(history_entry: Dict[str, Any]) -> Iterable[Tuple[str, str, Dict[str, Any]]]:
    outputs = history_entry.get("outputs")
    if not isinstance(outputs, dict):
        return
    for node_id, out in outputs.items():
        if not isinstance(node_id, str) or not isinstance(out, dict):
            continue
        for output_key, items in out.items():
            if not isinstance(items, list) or not items:
                continue
            if not all(isinstance(x, dict) and "filename" in x for x in items):
                continue
            for fileinfo in items:
                yield node_id, str(output_key), fileinfo


@dataclass(frozen=True)
class WorkflowCapabilities:
    kind: str
    has_load_image: bool
    has_save_image: bool
    has_save_video: bool


def detect_capabilities(prompt: Dict[str, Any]) -> WorkflowCapabilities:
    has_load_image = False
    has_save_image = False
    has_save_video = False
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        cls = as_str(node.get("class_type")).lower()
        if "loadimage" in cls:
            has_load_image = True
        if "saveimage" in cls:
            has_save_image = True
        if "savevideo" in cls or "createvideo" in cls:
            has_save_video = True

    kind = "unknown"
    if has_save_video and has_load_image:
        kind = "img2video"
    elif has_save_video and not has_load_image:
        kind = "txt2video"
    elif has_save_image and has_load_image:
        kind = "img2img"
    elif has_save_image and not has_load_image:
        kind = "txt2img"

    return WorkflowCapabilities(
        kind=kind,
        has_load_image=has_load_image,
        has_save_image=has_save_image,
        has_save_video=has_save_video,
    )

