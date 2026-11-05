"""Readable validation errors shared by HTTP tools and the lightweight MCP server."""


def validation_error(exc, tool):
    errors = []
    for error in exc.errors(include_url=False,include_context=False,include_input=False)[:8]:
        location = [str(value) for value in error["loc"]]
        hint = "请按工具 schema 修改该字段"
        if error["type"] == "extra_forbidden":
            hint = "删除多余字段；entry 没有 node_id，只有 storyline 的 nodes 内使用 node_id" if location[-1:] == ["node_id"] else "删除此字段；允许字段见当前工具 schema"
        elif error["type"] in {"literal_error","union_tag_invalid"}:
            hint = "使用工具 schema 中列出的枚举值"
        elif error["type"] == "missing":
            hint = "补充该必填字段；字段格式见当前工具 schema"
        errors.append({"field":".".join(location),"type":error["type"],"message":error["msg"],"hint":hint})
    return {"ok":False,"error":"invalid_arguments","data":{"message":"参数校验失败，请按字段提示修正后重试",
        "tool":tool,"errors":errors}}
