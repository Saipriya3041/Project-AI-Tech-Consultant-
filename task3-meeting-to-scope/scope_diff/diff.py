def compare_scopes(old, new):
    old_by_id = {item.get("id", str(index)): item for index, item in enumerate(old or [])}
    new_by_id = {item.get("id", str(index)): item for index, item in enumerate(new or [])}
    explicitly_removed = {identifier for identifier, item in new_by_id.items() if item.get("status") == "removed"}
    added = [
        {**item, "out_of_scope": True, "requires_separate_quote": True}
        for identifier, item in new_by_id.items()
        if identifier not in old_by_id and identifier not in explicitly_removed and item.get("status") != "removed"
    ]
    removed = [
        {**old_by_id[identifier], "change_note": new_by_id[identifier].get("description")}
        if identifier in explicitly_removed else item
        for identifier, item in old_by_id.items()
        if identifier not in new_by_id or identifier in explicitly_removed
    ]
    modified = [
        {"before": old_by_id[identifier], "after": item}
        for identifier, item in new_by_id.items()
        if identifier in old_by_id and old_by_id[identifier] != item and identifier not in explicitly_removed
    ]
    return {"added": added, "removed": removed, "modified": modified}
