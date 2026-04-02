from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .comfy_workflow import as_str, extract_prompt_and_extra, get_node_title, parse_node_input_ref, read_json


STANDARD_PARAMETER_ORDER = (
    "size",
    "width",
    "height",
    "steps",
    "cfg",
    "seed",
    "fps",
    "duration",
    "frames",
)


@dataclass(frozen=True)
class WorkflowParamSelector:
    class_type: str = ""
    title: str = ""
    input_key: str = ""


@dataclass(frozen=True)
class WorkflowParamTarget:
    ref: str = ""
    selector: WorkflowParamSelector | None = None
    part: str = ""
    transform: str = "direct"
    fps_param: str = "fps"
    round_mode: str = "round"


@dataclass(frozen=True)
class WorkflowParameterDefinition:
    name: str
    type: str
    default: Any = None
    description: str = ""
    required: bool = False
    minimum: Any = None
    maximum: Any = None
    maps: tuple[WorkflowParamTarget, ...] = ()


@dataclass(frozen=True)
class WorkflowParameterSpec:
    version: int
    kind: str
    parameters: dict[str, WorkflowParameterDefinition]
    path: Path
    prompt_node: str = ""
    negative_prompt_node: str = ""
    image_node: str = ""


def parameter_sidecar_dir(workflows_dir: Path) -> Path:
    return workflows_dir / ".comfyui2api"


def parameter_sidecar_path(workflows_dir: Path, workflow_path: Path) -> Path:
    return parameter_sidecar_dir(workflows_dir) / f"{workflow_path.stem}.params.json"


def workflow_path_from_sidecar(workflows_dir: Path, sidecar_path: Path) -> Path:
    name = sidecar_path.name
    suffix = ".params.json"
    if not name.endswith(suffix):
        raise ValueError(f"Not a workflow parameter sidecar: {sidecar_path}")
    return workflows_dir / f"{name[:-len(suffix)]}.json"


def _as_mapping(obj: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError(f"{context} must be an object")
    return obj


def _normalize_string(value: Any) -> str:
    return str(value or "").strip()


def _parse_size(value: Any) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        width = int(value[0])
        height = int(value[1])
    else:
        text = _normalize_string(value).lower().replace("*", "x")
        if "x" not in text:
            raise ValueError(f"Invalid size value: {value!r}")
        left, right = text.split("x", 1)
        width = int(left.strip())
        height = int(right.strip())
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid size value: {value!r}")
    return width, height


def normalize_parameter_value(definition: WorkflowParameterDefinition, raw_value: Any) -> Any:
    ptype = definition.type
    if ptype == "size":
        value: Any = _parse_size(raw_value)
        if definition.minimum is not None:
            min_width, min_height = _parse_size(definition.minimum)
            if value[0] < min_width or value[1] < min_height:
                raise ValueError(f"Parameter {definition.name!r} must be >= {definition.minimum}")
        if definition.maximum is not None:
            max_width, max_height = _parse_size(definition.maximum)
            if value[0] > max_width or value[1] > max_height:
                raise ValueError(f"Parameter {definition.name!r} must be <= {definition.maximum}")
        return value
    elif ptype == "int":
        value = int(raw_value)
    elif ptype == "float":
        value = float(raw_value)
    elif ptype == "image":
        value = _normalize_string(raw_value)
    elif ptype == "string":
        value = _normalize_string(raw_value)
    else:
        raise ValueError(f"Unsupported parameter type: {ptype}")

    if definition.minimum is not None and value < definition.minimum:
        raise ValueError(f"Parameter {definition.name!r} must be >= {definition.minimum}")
    if definition.maximum is not None and value > definition.maximum:
        raise ValueError(f"Parameter {definition.name!r} must be <= {definition.maximum}")
    return value


def _parse_selector(raw: Any, *, parameter_name: str, map_index: int) -> WorkflowParamSelector | None:
    if raw is None:
        return None
    obj = _as_mapping(raw, context=f"parameters.{parameter_name}.maps[{map_index}].selector")
    selector = WorkflowParamSelector(
        class_type=_normalize_string(obj.get("class_type")),
        title=_normalize_string(obj.get("title")),
        input_key=_normalize_string(obj.get("input_key")),
    )
    if not selector.class_type and not selector.title and not selector.input_key:
        raise ValueError(f"parameters.{parameter_name}.maps[{map_index}].selector must not be empty")
    return selector


def _parse_target(raw: Any, *, parameter_name: str, map_index: int) -> tuple[str, WorkflowParamSelector | None]:
    if isinstance(raw, str):
        return _normalize_string(raw), None
    if raw is None:
        return "", None
    obj = _as_mapping(raw, context=f"parameters.{parameter_name}.maps[{map_index}].target")
    ref = _normalize_string(obj.get("ref"))
    selector = _parse_selector(obj.get("selector"), parameter_name=parameter_name, map_index=map_index)
    return ref, selector


def _parse_map(raw: Any, *, parameter_name: str, map_index: int) -> WorkflowParamTarget:
    obj = _as_mapping(raw, context=f"parameters.{parameter_name}.maps[{map_index}]")
    ref = _normalize_string(obj.get("ref"))
    selector = _parse_selector(obj.get("selector"), parameter_name=parameter_name, map_index=map_index)

    target_value = obj.get("target")
    if target_value is not None:
        target_ref, target_selector = _parse_target(target_value, parameter_name=parameter_name, map_index=map_index)
        if target_ref:
            ref = target_ref
        if target_selector is not None:
            selector = target_selector

    if not ref and selector is None:
        raise ValueError(f"parameters.{parameter_name}.maps[{map_index}] needs a target or ref")

    part = _normalize_string(obj.get("part")).lower()
    if part and part not in {"width", "height"}:
        raise ValueError(f"Unsupported size part for {parameter_name!r}: {part!r}")

    transform = _normalize_string(obj.get("transform") or "direct").lower()
    if transform not in {"direct", "seconds_to_frames"}:
        raise ValueError(f"Unsupported transform for {parameter_name!r}: {transform!r}")

    round_mode = _normalize_string(obj.get("round") or "round").lower()
    if round_mode not in {"round", "ceil", "floor", "int"}:
        raise ValueError(f"Unsupported round mode for {parameter_name!r}: {round_mode!r}")

    return WorkflowParamTarget(
        ref=ref,
        selector=selector,
        part=part,
        transform=transform,
        fps_param=_normalize_string(obj.get("fps_param") or "fps") or "fps",
        round_mode=round_mode,
    )


def load_workflow_parameter_spec(
    *,
    workflows_dir: Path,
    workflow_path: Path,
    expected_kind: str = "",
) -> WorkflowParameterSpec | None:
    path = parameter_sidecar_path(workflows_dir, workflow_path)
    if not path.exists():
        return None

    obj = _as_mapping(read_json(path), context=str(path))
    version = int(obj.get("version") or 1)
    if version != 1:
        raise ValueError(f"Unsupported parameter mapping version: {version}")

    kind = _normalize_string(obj.get("kind") or expected_kind or "unknown")
    if expected_kind and kind and kind != expected_kind:
        raise ValueError(f"Parameter mapping kind mismatch: expected {expected_kind!r}, got {kind!r}")

    raw_parameters = _as_mapping(obj.get("parameters") or {}, context=f"{path}.parameters")
    parameters: dict[str, WorkflowParameterDefinition] = {}
    for name, raw_definition in raw_parameters.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Parameter names must be non-empty strings")
        param_name = name.strip()
        definition_obj = _as_mapping(raw_definition, context=f"parameters.{param_name}")
        ptype = _normalize_string(definition_obj.get("type") or "string").lower()
        maps_raw = definition_obj.get("maps") or []
        if not isinstance(maps_raw, list) or not maps_raw:
            raise ValueError(f"parameters.{param_name}.maps must be a non-empty array")
        maps = tuple(_parse_map(item, parameter_name=param_name, map_index=i) for i, item in enumerate(maps_raw))

        definition = WorkflowParameterDefinition(
            name=param_name,
            type=ptype,
            default=definition_obj.get("default"),
            description=_normalize_string(definition_obj.get("description")),
            required=bool(definition_obj.get("required") is True),
            minimum=definition_obj.get("minimum"),
            maximum=definition_obj.get("maximum"),
            maps=maps,
        )
        if definition.default is not None:
            normalize_parameter_value(definition, definition.default)
        parameters[param_name] = definition

    return WorkflowParameterSpec(
        version=version,
        kind=kind,
        parameters=parameters,
        path=path.resolve(),
        prompt_node=_normalize_string(obj.get("prompt_node")),
        negative_prompt_node=_normalize_string(obj.get("negative_prompt_node")),
        image_node=_normalize_string(obj.get("image_node")),
    )


def _ordered_parameter_names(parameter_names: Iterable[str]) -> list[str]:
    names = list(parameter_names)
    ordered = [name for name in STANDARD_PARAMETER_ORDER if name in names]
    ordered.extend(name for name in names if name not in ordered)
    return ordered


def _selector_matches(node: dict[str, Any], selector: WorkflowParamSelector) -> bool:
    if selector.class_type and as_str(node.get("class_type")).lower() != selector.class_type.lower():
        return False
    if selector.title and get_node_title(node).lower() != selector.title.lower():
        return False
    if selector.input_key:
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or selector.input_key not in inputs:
            return False
    return True


def _resolve_selector_target(prompt: dict[str, Any], selector: WorkflowParamSelector) -> tuple[str, str]:
    matches: list[tuple[str, str]] = []
    for node_id, node in prompt.items():
        if not isinstance(node_id, str) or not isinstance(node, dict):
            continue
        if not _selector_matches(node, selector):
            continue
        input_key = selector.input_key
        if not input_key:
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                continue
            candidates = [key for key in inputs.keys() if isinstance(key, str)]
            if len(candidates) != 1:
                continue
            input_key = candidates[0]
        matches.append((node_id, input_key))

    if not matches:
        raise KeyError("No workflow node matched parameter selector")
    if len(matches) > 1:
        lines = [f"{node_id}.{input_key}" for node_id, input_key in matches[:12]]
        raise KeyError("Ambiguous parameter selector. Candidates: " + ", ".join(lines))
    return matches[0]


def _resolve_target(prompt: dict[str, Any], mapping: WorkflowParamTarget) -> tuple[str, str]:
    if mapping.ref:
        node_id, input_key = parse_node_input_ref(mapping.ref, default_input="value")
        node = prompt.get(node_id)
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if isinstance(inputs, dict) and input_key in inputs:
            return node_id, input_key
        if mapping.selector is None:
            return node_id, input_key
    if mapping.selector is None:
        raise KeyError("Missing parameter target selector")
    return _resolve_selector_target(prompt, mapping.selector)


def _round_number(value: float, mode: str) -> int:
    if mode == "ceil":
        return int(math.ceil(value))
    if mode == "floor":
        return int(math.floor(value))
    if mode == "int":
        return int(value)
    return int(round(value))


def _mapped_value(
    *,
    parameter_name: str,
    parameter_value: Any,
    mapping: WorkflowParamTarget,
    resolved_params: Mapping[str, Any],
) -> Any:
    if mapping.transform == "seconds_to_frames":
        fps_name = mapping.fps_param or "fps"
        fps_value = resolved_params.get(fps_name)
        if fps_value is None:
            raise ValueError(f"Parameter {parameter_name!r} needs {fps_name!r} for seconds_to_frames")
        return _round_number(float(parameter_value) * float(fps_value), mapping.round_mode)

    if mapping.part:
        width, height = _parse_size(parameter_value)
        return width if mapping.part == "width" else height

    return parameter_value


def resolve_standard_overrides(
    *,
    workflow_obj: Any,
    spec: WorkflowParameterSpec | None,
    request_params: Mapping[str, Any] | None,
) -> list[tuple[str, str, Any]]:
    if spec is None or not spec.parameters:
        return []

    prompt, _extra_data = extract_prompt_and_extra(workflow_obj)
    raw_request_params = dict(request_params or {})
    resolved_params: dict[str, Any] = {}

    for name, definition in spec.parameters.items():
        if definition.default is not None:
            resolved_params[name] = normalize_parameter_value(definition, definition.default)

    for name, raw_value in raw_request_params.items():
        definition = spec.parameters.get(name)
        if definition is None:
            continue
        resolved_params[name] = normalize_parameter_value(definition, raw_value)

    overrides: list[tuple[str, str, Any]] = []
    for name in _ordered_parameter_names(spec.parameters.keys()):
        if name not in resolved_params:
            continue
        definition = spec.parameters[name]
        value = resolved_params[name]
        for mapping in definition.maps:
            node_id, input_key = _resolve_target(prompt, mapping)
            mapped_value = _mapped_value(
                parameter_name=name,
                parameter_value=value,
                mapping=mapping,
                resolved_params=resolved_params,
            )
            overrides.append((node_id, input_key, mapped_value))
    return overrides


def _public_selector(selector: WorkflowParamSelector | None) -> dict[str, Any] | None:
    if selector is None:
        return None
    return {
        "class_type": selector.class_type or None,
        "title": selector.title or None,
        "input_key": selector.input_key or None,
    }


def _normalize_input_key(input_key: str) -> str:
    return "".join(ch for ch in str(input_key or "").lower() if ch.isalnum())


def _is_numeric_value(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _candidate_selector(node: dict[str, Any], input_key: str) -> WorkflowParamSelector:
    return WorkflowParamSelector(
        class_type=as_str(node.get("class_type")),
        title=get_node_title(node),
        input_key=input_key,
    )


def _candidate_map_dict(
    node_id: str,
    node: dict[str, Any],
    input_key: str,
    *,
    part: str = "",
    transform: str = "direct",
    fps_param: str = "fps",
    round_mode: str = "round",
) -> dict[str, Any]:
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    selector = _candidate_selector(node, input_key)
    return {
        "ref": f"{node_id}.{input_key}",
        "selector": _public_selector(selector),
        "input_key": input_key,
        "current": inputs.get(input_key) if isinstance(inputs, dict) else None,
        "part": part or None,
        "transform": transform,
        "fps_param": fps_param if transform == "seconds_to_frames" else None,
        "round": round_mode if transform == "seconds_to_frames" else None,
    }


def _append_candidate(
    buckets: dict[str, list[dict[str, Any]]],
    seen: dict[str, set[tuple[Any, ...]]],
    *,
    parameter_name: str,
    score: int,
    reason: str,
    node_id: str,
    node: dict[str, Any],
    maps: list[dict[str, Any]],
    paired_fps_ref: str | None = None,
) -> None:
    key = (tuple((m.get("ref"), m.get("part"), m.get("transform"), m.get("fps_param")) for m in maps), paired_fps_ref)
    if key in seen.setdefault(parameter_name, set()):
        return
    seen[parameter_name].add(key)
    buckets.setdefault(parameter_name, []).append(
        {
            "score": score,
            "reason": reason,
            "node_id": node_id,
            "class_type": as_str(node.get("class_type")) or None,
            "title": get_node_title(node) or None,
            "paired_fps_ref": paired_fps_ref,
            "maps": maps,
        }
    )


def _score_input_candidate(parameter_name: str, *, input_key: str, class_type: str, title: str) -> int:
    score = 50
    norm = _normalize_input_key(input_key)
    title_l = title.lower()
    cls_l = class_type.lower()

    exact_keys = {
        "width": {"width"},
        "height": {"height"},
        "steps": {"steps"},
        "cfg": {"cfg"},
        "seed": {"seed"},
        "fps": {"fps"},
        "frames": {"frames"},
        "duration": {"duration", "seconds"},
    }
    aliases = {
        "width": {"imagewidth", "latentwidth"},
        "height": {"imageheight", "latentheight"},
        "steps": {"numsteps"},
        "cfg": {"cfgscale", "guidance", "guidancescale"},
        "seed": {"noiseseed", "randomseed"},
        "fps": {"framerate"},
        "frames": {"numframes", "framecount"},
        "duration": {"lengthseconds"},
    }
    semantic_tokens = {
        "size": ("latent", "image", "size"),
        "width": ("latent", "image", "size"),
        "height": ("latent", "image", "size"),
        "steps": ("sampler", "ksampler"),
        "cfg": ("sampler", "guidance", "ksampler"),
        "seed": ("sampler", "noise", "ksampler"),
        "fps": ("video", "frame", "combine"),
        "frames": ("video", "frame", "combine"),
        "duration": ("video", "duration", "seconds"),
    }

    if norm in exact_keys.get(parameter_name, set()):
        score += 20
    elif norm in aliases.get(parameter_name, set()):
        score += 10
    if any(token in title_l or token in cls_l for token in semantic_tokens.get(parameter_name, ())):
        score += 15
    if parameter_name in {"width", "height"} and "empty" in cls_l:
        score += 5
    return score


def detect_parameter_candidates(workflow_obj: Any) -> dict[str, list[dict[str, Any]]]:
    prompt, _extra_data = extract_prompt_and_extra(workflow_obj)
    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in STANDARD_PARAMETER_ORDER}
    seen: dict[str, set[tuple[Any, ...]]] = {}

    width_entries: list[tuple[int, str, dict[str, Any], str]] = []
    height_entries: list[tuple[int, str, dict[str, Any], str]] = []
    fps_entries: list[tuple[int, str, dict[str, Any], str]] = []
    frames_entries: list[tuple[int, str, dict[str, Any], str]] = []

    for node_id, node in prompt.items():
        if not isinstance(node_id, str) or not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        cls = as_str(node.get("class_type"))
        title = get_node_title(node)
        node_width: list[tuple[int, str]] = []
        node_height: list[tuple[int, str]] = []

        for input_key, value in inputs.items():
            if not isinstance(input_key, str) or not _is_numeric_value(value):
                continue
            normalized = _normalize_input_key(input_key)

            def _matches(names: set[str]) -> bool:
                return normalized in names

            if _matches({"width", "imagewidth", "latentwidth"}):
                score = _score_input_candidate("width", input_key=input_key, class_type=cls, title=title)
                node_width.append((score, input_key))
                width_entries.append((score, node_id, node, input_key))
                _append_candidate(
                    buckets,
                    seen,
                    parameter_name="width",
                    score=score,
                    reason=f"matched numeric input '{input_key}'",
                    node_id=node_id,
                    node=node,
                    maps=[_candidate_map_dict(node_id, node, input_key)],
                )
            elif _matches({"height", "imageheight", "latentheight"}):
                score = _score_input_candidate("height", input_key=input_key, class_type=cls, title=title)
                node_height.append((score, input_key))
                height_entries.append((score, node_id, node, input_key))
                _append_candidate(
                    buckets,
                    seen,
                    parameter_name="height",
                    score=score,
                    reason=f"matched numeric input '{input_key}'",
                    node_id=node_id,
                    node=node,
                    maps=[_candidate_map_dict(node_id, node, input_key)],
                )
            elif _matches({"steps", "numsteps"}):
                score = _score_input_candidate("steps", input_key=input_key, class_type=cls, title=title)
                _append_candidate(
                    buckets,
                    seen,
                    parameter_name="steps",
                    score=score,
                    reason=f"matched numeric input '{input_key}'",
                    node_id=node_id,
                    node=node,
                    maps=[_candidate_map_dict(node_id, node, input_key)],
                )
            elif _matches({"cfg", "cfgscale", "guidance", "guidancescale"}):
                score = _score_input_candidate("cfg", input_key=input_key, class_type=cls, title=title)
                _append_candidate(
                    buckets,
                    seen,
                    parameter_name="cfg",
                    score=score,
                    reason=f"matched numeric input '{input_key}'",
                    node_id=node_id,
                    node=node,
                    maps=[_candidate_map_dict(node_id, node, input_key)],
                )
            elif _matches({"seed", "noiseseed", "randomseed"}):
                score = _score_input_candidate("seed", input_key=input_key, class_type=cls, title=title)
                _append_candidate(
                    buckets,
                    seen,
                    parameter_name="seed",
                    score=score,
                    reason=f"matched numeric input '{input_key}'",
                    node_id=node_id,
                    node=node,
                    maps=[_candidate_map_dict(node_id, node, input_key)],
                )
            elif _matches({"fps", "framerate"}):
                score = _score_input_candidate("fps", input_key=input_key, class_type=cls, title=title)
                fps_entries.append((score, node_id, node, input_key))
                _append_candidate(
                    buckets,
                    seen,
                    parameter_name="fps",
                    score=score,
                    reason=f"matched numeric input '{input_key}'",
                    node_id=node_id,
                    node=node,
                    maps=[_candidate_map_dict(node_id, node, input_key)],
                )
            elif _matches({"frames", "numframes", "framecount"}):
                score = _score_input_candidate("frames", input_key=input_key, class_type=cls, title=title)
                frames_entries.append((score, node_id, node, input_key))
                _append_candidate(
                    buckets,
                    seen,
                    parameter_name="frames",
                    score=score,
                    reason=f"matched numeric input '{input_key}'",
                    node_id=node_id,
                    node=node,
                    maps=[_candidate_map_dict(node_id, node, input_key)],
                )
            elif _matches({"duration", "seconds", "lengthseconds"}):
                score = _score_input_candidate("duration", input_key=input_key, class_type=cls, title=title)
                _append_candidate(
                    buckets,
                    seen,
                    parameter_name="duration",
                    score=score,
                    reason=f"matched numeric input '{input_key}'",
                    node_id=node_id,
                    node=node,
                    maps=[_candidate_map_dict(node_id, node, input_key)],
                )

        if node_width and node_height:
            width_score, width_key = sorted(node_width, key=lambda item: item[0], reverse=True)[0]
            height_score, height_key = sorted(node_height, key=lambda item: item[0], reverse=True)[0]
            _append_candidate(
                buckets,
                seen,
                parameter_name="size",
                score=width_score + height_score + 20,
                reason="paired width and height inputs on the same node",
                node_id=node_id,
                node=node,
                maps=[
                    _candidate_map_dict(node_id, node, width_key, part="width"),
                    _candidate_map_dict(node_id, node, height_key, part="height"),
                ],
            )

    best_fps = sorted(fps_entries, key=lambda item: item[0], reverse=True)
    for frame_score, node_id, node, frame_key in sorted(frames_entries, key=lambda item: item[0], reverse=True):
        same_node_fps = [item for item in best_fps if item[1] == node_id]
        paired = same_node_fps[0] if same_node_fps else (best_fps[0] if best_fps else None)
        duration_score = frame_score + 15
        reason = "convert duration seconds to frames"
        paired_ref = None
        if paired is not None:
            duration_score += paired[0] // 2
            paired_ref = f"{paired[1]}.{paired[3]}"
            reason = (
                "convert duration seconds to frames using fps from the same node"
                if paired[1] == node_id
                else "convert duration seconds to frames using the best fps candidate"
            )
        _append_candidate(
            buckets,
            seen,
            parameter_name="duration",
            score=duration_score,
            reason=reason,
            node_id=node_id,
            node=node,
            paired_fps_ref=paired_ref,
            maps=[
                _candidate_map_dict(
                    node_id,
                    node,
                    frame_key,
                    transform="seconds_to_frames",
                    fps_param="fps",
                    round_mode="ceil",
                )
            ],
        )

    output: dict[str, list[dict[str, Any]]] = {}
    for name in STANDARD_PARAMETER_ORDER:
        items = buckets.get(name) or []
        items.sort(key=lambda item: (item["score"], item.get("node_id") or ""), reverse=True)
        output[name] = items[:8]
    return output


def _parameter_type(name: str) -> str:
    if name == "size":
        return "size"
    if name in {"cfg", "duration"}:
        return "float"
    return "int"


def _parameter_description(name: str) -> str:
    descriptions = {
        "size": "Image or latent size as WIDTHxHEIGHT.",
        "width": "Output width.",
        "height": "Output height.",
        "steps": "Sampler steps.",
        "cfg": "Guidance or CFG scale.",
        "seed": "Random seed.",
        "fps": "Video frames per second.",
        "duration": "Video duration in seconds.",
        "frames": "Video frame count.",
    }
    return descriptions.get(name, "")


def _map_to_template_entry(map_item: Mapping[str, Any]) -> dict[str, Any]:
    target: dict[str, Any] = {}
    if map_item.get("ref"):
        target["ref"] = map_item.get("ref")
    selector = map_item.get("selector")
    if isinstance(selector, dict) and any(selector.values()):
        target["selector"] = selector

    entry: dict[str, Any] = {"target": target if target else (map_item.get("ref") or "")}
    if map_item.get("part"):
        entry["part"] = map_item["part"]
    if map_item.get("transform") and map_item.get("transform") != "direct":
        entry["transform"] = map_item["transform"]
        if map_item.get("fps_param"):
            entry["fps_param"] = map_item["fps_param"]
        if map_item.get("round"):
            entry["round"] = map_item["round"]
    return entry


def _candidate_default(parameter_name: str, candidate: Mapping[str, Any], candidates_by_name: Mapping[str, list[dict[str, Any]]]) -> Any:
    maps = candidate.get("maps")
    if not isinstance(maps, list) or not maps:
        return None

    if parameter_name == "size":
        if len(maps) < 2:
            return None
        width = maps[0].get("current")
        height = maps[1].get("current")
        if _is_numeric_value(width) and _is_numeric_value(height):
            return f"{int(width)}x{int(height)}"
        return None

    current = maps[0].get("current")
    if parameter_name == "duration":
        frames = maps[0].get("current")
        paired_ref = candidate.get("paired_fps_ref")
        if not _is_numeric_value(frames) or not isinstance(paired_ref, str) or not paired_ref:
            return None
        fps_candidates = candidates_by_name.get("fps") or []
        for fps_candidate in fps_candidates:
            fps_maps = fps_candidate.get("maps")
            if not isinstance(fps_maps, list) or not fps_maps:
                continue
            if fps_maps[0].get("ref") != paired_ref:
                continue
            fps_value = fps_maps[0].get("current")
            if _is_numeric_value(fps_value) and float(fps_value) > 0:
                return round(float(frames) / float(fps_value), 3)
        return None

    if not _is_numeric_value(current):
        return None
    if parameter_name == "cfg":
        return float(current)
    return int(current)


def _definition_to_template_entry(definition: WorkflowParameterDefinition) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": definition.type,
        "maps": [
            {
                **_map_to_template_entry(
                    {
                        "ref": item.ref or None,
                        "selector": _public_selector(item.selector),
                        "part": item.part or None,
                        "transform": item.transform,
                        "fps_param": item.fps_param if item.transform == "seconds_to_frames" else None,
                        "round": item.round_mode if item.transform == "seconds_to_frames" else None,
                    }
                )
            }
            for item in definition.maps
        ],
    }
    if definition.default is not None:
        entry["default"] = definition.default
    if definition.description:
        entry["description"] = definition.description
    elif _parameter_description(definition.name):
        entry["description"] = _parameter_description(definition.name)
    if definition.required:
        entry["required"] = True
    if definition.minimum is not None:
        entry["minimum"] = definition.minimum
    if definition.maximum is not None:
        entry["maximum"] = definition.maximum
    return entry


def generate_parameter_template(
    *,
    workflow_obj: Any,
    kind: str,
    spec: WorkflowParameterSpec | None = None,
) -> dict[str, Any]:
    candidates_by_name = detect_parameter_candidates(workflow_obj)
    parameters: dict[str, Any] = {}

    if spec is not None:
        for name in _ordered_parameter_names(spec.parameters.keys()):
            entry = _definition_to_template_entry(spec.parameters[name])
            candidates = candidates_by_name.get(name) or []
            if "default" not in entry and candidates:
                default = _candidate_default(name, candidates[0], candidates_by_name)
                if default is not None:
                    entry["default"] = default
            if not entry.get("description") and _parameter_description(name):
                entry["description"] = _parameter_description(name)
            parameters[name] = entry

    for name in STANDARD_PARAMETER_ORDER:
        if name in parameters:
            continue
        candidates = candidates_by_name.get(name) or []
        if not candidates:
            continue
        top = candidates[0]
        entry: dict[str, Any] = {
            "type": _parameter_type(name),
            "description": _parameter_description(name),
            "maps": [_map_to_template_entry(map_item) for map_item in top.get("maps") or []],
        }
        default = _candidate_default(name, top, candidates_by_name)
        if default is not None:
            entry["default"] = default
        parameters[name] = entry

    return {
        "version": 1,
        "kind": kind or (spec.kind if spec is not None else "unknown"),
        "parameters": parameters,
    }


def public_parameter_spec(spec: WorkflowParameterSpec | None) -> dict[str, Any]:
    if spec is None:
        return {
            "version": 1,
            "kind": None,
            "path": None,
            "input_targets": {"prompt_node": None, "negative_prompt_node": None, "image_node": None},
            "parameters": [],
        }

    items = []
    for name in _ordered_parameter_names(spec.parameters.keys()):
        definition = spec.parameters[name]
        items.append(
            {
                "name": name,
                "type": definition.type,
                "default": definition.default,
                "description": definition.description or None,
                "required": definition.required,
                "minimum": definition.minimum,
                "maximum": definition.maximum,
                "maps": [
                    {
                        "ref": item.ref or None,
                        "selector": _public_selector(item.selector),
                        "part": item.part or None,
                        "transform": item.transform,
                        "fps_param": item.fps_param if item.transform == "seconds_to_frames" else None,
                        "round": item.round_mode if item.transform == "seconds_to_frames" else None,
                    }
                    for item in definition.maps
                ],
            }
        )

    return {
        "version": spec.version,
        "kind": spec.kind,
        "path": str(spec.path),
        "input_targets": {
            "prompt_node": spec.prompt_node or None,
            "negative_prompt_node": spec.negative_prompt_node or None,
            "image_node": spec.image_node or None,
        },
        "parameters": items,
    }
