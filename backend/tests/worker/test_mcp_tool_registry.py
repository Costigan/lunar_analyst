from __future__ import annotations

from types import SimpleNamespace

from backend.services.assistant.tool_registry import (
    REGISTRY_TOOL_EXECUTORS,
    action_type_for_tool,
    execute_tool,
    list_tools_for_model,
    list_tools_schema,
    select_tool_names_for_prompt,
    tool_argument_schema_for_model,
    tool_argument_schema_for_tool,
)


def _input_reference_properties(schema: dict[str, object]) -> dict[str, object]:
    inputs = schema["properties"]["inputs"]  # type: ignore[index]
    additional = inputs["additionalProperties"]  # type: ignore[index]
    if isinstance(additional, dict) and "$ref" in additional:
        ref_name = str(additional["$ref"]).rsplit("/", 1)[-1]
        return schema["$defs"][ref_name]["properties"]  # type: ignore[index]
    return additional["properties"]  # type: ignore[index]


def test_tool_schema_contains_capabilities_and_mutating_annotations() -> None:
    tools = list_tools_schema()
    by_name = {item["name"]: item for item in tools}
    assert "capabilities.describe" in by_name
    assert "tools.search" in by_name
    assert "tools.describe" in by_name
    assert "scenario.set_current" in by_name
    assert "scenario.run_script" in by_name
    assert "raster.calculate" in by_name
    assert "raster.transform" in by_name
    assert "raster.transform.prefilter" not in by_name
    assert "raster_transform_prefilter" not in by_name
    assert "generate_psr_raster" in by_name
    assert "ping" not in by_name
    assert "runs.get_logs" in by_name
    assert by_name["capabilities.describe"]["requires_confirmation"] is False
    assert by_name["job.launch"]["requires_confirmation"] is True
    assert by_name["scenario.run_script"]["requires_confirmation"] is True
    assert by_name["raster.calculate"]["requires_confirmation"] is True
    assert by_name["raster.transform"]["requires_confirmation"] is True
    assert by_name["scenario.write_script"]["confirmation_mode"] == "conditional"
    assert by_name["generate_psr_raster"]["visibility"] == "public"


def test_action_type_lookup() -> None:
    assert action_type_for_tool("job.launch") is not None
    assert action_type_for_tool("scenario.list") is None
    assert action_type_for_tool("scenario.run_script") is not None
    assert action_type_for_tool("raster.calculate") is not None
    assert action_type_for_tool("raster.transform") is not None


def test_raster_calculate_schema_includes_temporal_inputs() -> None:
    schema = tool_argument_schema_for_tool("raster.calculate")
    input_schema = _input_reference_properties(schema)
    assert "signal" in input_schema
    assert "time_start_utc" in schema["properties"]
    assert "time_stop_utc" in schema["properties"]
    assert "time_step_hours" in schema["properties"]
    assert "publish_layer" in schema["properties"]
    publish_layer = schema["properties"]["publish_layer"]
    publish_ref = str(publish_layer["anyOf"][0]["$ref"]).rsplit("/", 1)[-1]
    publish_props = schema["$defs"][publish_ref]["properties"]
    assert "enabled" in publish_props
    assert "visible" in publish_props


def test_raster_transform_schema_includes_script_and_partitioning_controls() -> None:
    schema = tool_argument_schema_for_tool("raster.transform")
    assert set(schema["required"]) == {"scenario_id", "script", "inputs"}
    input_schema = _input_reference_properties(schema)
    assert "signal" in input_schema
    assert input_schema["kind"]["anyOf"][0]["const"] == "times"
    assert "temporal_source" in input_schema
    assert "times" in input_schema
    assert "start_utc" in input_schema
    assert "stop_utc" in input_schema
    assert "step_hours" in input_schema
    assert schema["properties"]["spatial_partitioning"]["enum"] == ["auto", "allowed", "forbidden"]
    assert schema["properties"]["time_partitioning"]["enum"] == ["auto", "allowed", "forbidden"]


def test_model_tool_schemas_do_not_use_permissive_fallback() -> None:
    fallback = {"type": "object", "additionalProperties": True}
    tool_entries = list_tools_for_model()
    offenders: list[str] = []
    for entry in tool_entries:
        fn = entry.get("function", {})
        name = str(fn.get("name", "")).strip()
        schema = fn.get("parameters")
        if schema == fallback:
            offenders.append(name)
    assert offenders == []


def test_internal_raster_transform_prefilter_is_not_exposed_in_model_tool_list() -> None:
    tool_entries = list_tools_for_model()
    names = {str(item.get("function", {}).get("name", "")).strip() for item in tool_entries}
    assert all("prefilter" not in name for name in names)


def test_model_facing_raster_calculate_schema_is_narrowed() -> None:
    schema = tool_argument_schema_for_model("raster.calculate")
    props = schema["properties"]
    assert "scenario_id" in props
    assert "expression" in props
    assert "inputs" in props
    assert "output_relative_path" in props
    assert "publish_layer" in props
    assert "overwrite_mode" in props
    assert "time_start_utc" not in props
    assert "time_stop_utc" not in props
    assert "time_step_hours" not in props
    assert "patch_width" not in props
    assert "chunk_time_count" not in props
    publish_props = props["publish_layer"]["properties"]
    assert "transparent_background" in publish_props


def test_model_facing_raster_transform_schema_uses_overwrite_mode() -> None:
    schema = tool_argument_schema_for_model("raster.transform")
    props = schema["properties"]
    assert "overwrite_mode" in props
    assert "overwrite" not in props


def test_model_tool_descriptions_include_mask_and_visibility_guidance() -> None:
    tool_entries = list_tools_for_model()
    by_name = {
        str(item.get("function", {}).get("name", "")).strip(): str(
            item.get("function", {}).get("description", "")
        )
        for item in tool_entries
    }
    raster_calc_desc = by_name["raster.calculate"]
    layer_update_desc = by_name["layer.update_state"]
    import_desc = by_name["scenario.import_geotiff"]
    assert "variables that exactly match `inputs` keys" in raster_calc_desc
    assert "where(slope <= X, 1, nodata())" in raster_calc_desc
    assert "avoid read-only inventory loops" in layer_update_desc
    assert "do not re-import an already imported product" in import_desc


def test_tools_search_and_describe_contracts_work() -> None:
    tools = list_tools_schema()
    by_name = {item["name"]: item for item in tools}
    search_schema = tool_argument_schema_for_tool("tools.search")
    describe_schema = tool_argument_schema_for_tool("tools.describe")
    assert search_schema["required"] == ["query"]
    assert describe_schema["required"] == ["tool_name"]
    assert "Search tools by name/keywords" in by_name["tools.search"]["description"]
    assert "Describe a specific tool" in by_name["tools.describe"]["description"]


def test_select_tool_names_for_prompt_includes_lookup_tools() -> None:
    selected = select_tool_names_for_prompt(
        prompt="Create a slope mask and make non-selected pixels transparent.",
        max_tools=12,
    )
    assert "tools.search" in selected
    assert "tools.describe" in selected
    assert "raster.calculate" in selected


def test_registry_dispatch_handles_lookup_tools() -> None:
    assert "capabilities.describe" in REGISTRY_TOOL_EXECUTORS
    assert "tools.search" in REGISTRY_TOOL_EXECUTORS
    assert "tools.describe" in REGISTRY_TOOL_EXECUTORS
    assert "scenario.rag_ingest" in REGISTRY_TOOL_EXECUTORS
    assert "scenario.run_script" in REGISTRY_TOOL_EXECUTORS
    assert "scenario.run_marimo_notebook" in REGISTRY_TOOL_EXECUTORS
    assert "scenario.write_script" in REGISTRY_TOOL_EXECUTORS
    assert "scenario.write_run_script" in REGISTRY_TOOL_EXECUTORS
    assert "jobs.list_predefined" in REGISTRY_TOOL_EXECUTORS
    assert "jobs.run_predefined" in REGISTRY_TOOL_EXECUTORS
    assert "runs.get_status" in REGISTRY_TOOL_EXECUTORS
    assert "runs.get_logs" in REGISTRY_TOOL_EXECUTORS
    assert "runs.cancel" in REGISTRY_TOOL_EXECUTORS
    assert "job.launch" in REGISTRY_TOOL_EXECUTORS
    assert "job.cancel" in REGISTRY_TOOL_EXECUTORS
    assert "product.list" in REGISTRY_TOOL_EXECUTORS
    assert "product.files" in REGISTRY_TOOL_EXECUTORS
    assert "artifact.describe_geotiff" in REGISTRY_TOOL_EXECUTORS
    assert "artifact.preview_geotiff" in REGISTRY_TOOL_EXECUTORS
    assert "artifact.stats_geotiff" in REGISTRY_TOOL_EXECUTORS
    assert "artifact.describe_table" in REGISTRY_TOOL_EXECUTORS
    assert "artifact.describe_plot" in REGISTRY_TOOL_EXECUTORS
    assert "scenario.import_geotiff" in REGISTRY_TOOL_EXECUTORS
    assert "scenario.move_path" in REGISTRY_TOOL_EXECUTORS
    assert "colormap.list" in REGISTRY_TOOL_EXECUTORS
    assert "colormap.create_simple" in REGISTRY_TOOL_EXECUTORS
    assert "colormap.save_scenario" in REGISTRY_TOOL_EXECUTORS
    assert "layer.apply_colormap" in REGISTRY_TOOL_EXECUTORS
    assert "layer.list_visible" in REGISTRY_TOOL_EXECUTORS
    assert "layer.update_state" in REGISTRY_TOOL_EXECUTORS

    services = SimpleNamespace()
    caps = execute_tool(services, tool_name="capabilities.describe", arguments={})
    assert int(caps.get("tool_count", 0)) > 0

    search = execute_tool(services, tool_name="tools.search", arguments={"query": "scenario"})
    assert int(search.get("count", 0)) > 0


def test_static_mutating_and_product_tools_have_strict_required_fields() -> None:
    layer_update_schema = tool_argument_schema_for_tool("layer.update_state")
    assert layer_update_schema["type"] == "object"
    assert layer_update_schema["additionalProperties"] is False
    assert "layer_id" in layer_update_schema["properties"]
    assert "scenario_id" in layer_update_schema["properties"]
    assert "layer_name" in layer_update_schema["properties"]
    assert "anyOf" not in layer_update_schema
    assert tool_argument_schema_for_tool("product.list")["required"] == ["scenario_id"]
    assert tool_argument_schema_for_tool("product.files")["required"] == ["product_id"]
    assert tool_argument_schema_for_tool("scenario.import_geotiff")["required"] == ["scenario_id", "source_path"]
    assert tool_argument_schema_for_tool("scenario.move_path")["required"] == [
        "scenario_id",
        "source_relative_path",
        "target_relative_path",
    ]
    run_script_schema = tool_argument_schema_for_tool("scenario.run_script")
    run_script_props = run_script_schema["properties"]
    assert run_script_props["runtime_mode"]["enum"] == ["osgeo", "moonlib"]
    write_run_schema = tool_argument_schema_for_tool("scenario.write_run_script")
    write_run_props = write_run_schema["properties"]
    assert write_run_props["runtime_mode"]["enum"] == ["osgeo", "moonlib"]


def test_model_tool_schemas_arrays_have_items() -> None:
    tool_entries = list_tools_for_model()
    offenders: list[str] = []

    def _recurse(node: object, path: list[str], tool_name: str) -> None:
        if isinstance(node, dict):
            t = str(node.get("type", "")).strip().lower()
            if t == "array":
                if "items" not in node:
                    offenders.append(f"{tool_name}:{'/'.join(path)}")
                else:
                    _recurse(node.get("items"), path + ["items"], tool_name)
            props = node.get("properties")
            if isinstance(props, dict):
                for prop_name, prop_schema in props.items():
                    _recurse(prop_schema, path + ["properties", prop_name], tool_name)
            addp = node.get("additionalProperties")
            if isinstance(addp, dict):
                _recurse(addp, path + ["additionalProperties"], tool_name)
            for comb in ("anyOf", "oneOf", "allOf"):
                comb_val = node.get(comb)
                if isinstance(comb_val, list):
                    for idx, item in enumerate(comb_val):
                        _recurse(item, path + [comb, str(idx)], tool_name)
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                _recurse(item, path + [str(idx)], tool_name)

    for entry in tool_entries:
        fn = entry.get("function", {})
        name = str(fn.get("name", "")).strip()
        params = fn.get("parameters")
        if isinstance(params, dict):
            _recurse(params, ["function", "parameters"], name)

    assert offenders == [], "Found array nodes without 'items': " + ", ".join(offenders)


def test_colormap_create_simple_has_items() -> None:
    schema = tool_argument_schema_for_model("colormap.create_simple")
    props = schema.get("properties", {})
    assert "stops" in props
    stops = props["stops"]
    assert isinstance(stops, dict) and "items" in stops
    params = props.get("parameters")
    assert params is None or (isinstance(params, dict) and "items" in params)
