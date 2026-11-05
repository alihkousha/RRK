from apex.state import ApexState


def test_update_from_html_unescapes_values():
    html = (
        '<input type="hidden" value="7564754836135" id="pInstance" />'
        '<input type="hidden" id="pPageItemsProtected" value="UDBf&#x2F;-mMa" />'
        '<input type="hidden" id="pPageItemsRowVersion" value="" />'
    )
    state = ApexState()
    state.update_from_html(html)
    assert state.instance == "7564754836135"
    assert state.page_items_protected == "UDBf/-mMa"
    assert state.page_items_row_version is None
