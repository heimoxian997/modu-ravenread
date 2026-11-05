"""Describe evidence provenance without assigning a numerical confidence score."""
LABELS = {"explicit":"原文明示","interpretation":"解释推断","mixed":"混合依据","unclassified":"未标注"}


def aggregate(values):
    values = set(values)
    if not values or "unclassified" in values:return "unclassified"
    return next(iter(values)) if len(values)==1 else "mixed"


def entry(content):
    basis = content.get("basis","unclassified")
    return {"basis":basis,"basis_label":LABELS[basis],"perspective":content.get("perspective")}


def node(content, latest, stale=False, labels=True):
    basis = content.get("basis","unclassified")
    evidence = []
    perspectives = []
    for ref in content["evidence"]:
        record = (latest or {}).get(ref["note_id"])
        if record is None or record.version!=ref["version"]:
            evidence.append("unclassified")
            continue
        evidence.append(record.content.get("basis","unclassified"))
        if record.content.get("perspective"):perspectives.append(record.content["perspective"])
    support = aggregate(evidence)
    if stale:basis,support = "unclassified","unclassified"
    elif basis=="explicit" and support!="explicit":basis = support
    result = {"basis":basis,"perspective":None if stale else content.get("perspective"),"evidence_basis":support}
    if labels:result["basis_label"] = LABELS[basis]
    if perspectives and not stale:result["evidence_perspectives"] = list(dict.fromkeys(perspectives))
    return result
