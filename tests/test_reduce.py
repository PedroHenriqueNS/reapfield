from conftest import fixture

from reapfield.reduce import CAP, reduce_dom


def test_reduction_stays_under_cap():
    out = reduce_dom(fixture("listing.html"))
    assert 0 < len(out) <= CAP
    assert len(out) < len(fixture("listing.html"))


def test_repeated_siblings_collapse():
    """20 identical product cards become 2 plus a marker -- the big win on listings."""
    out = reduce_dom(fixture("listing.html"))
    assert "more identical" in out
    assert out.count("product_pod") <= 6  # 2 kept, not 20


def test_scripts_and_styles_are_dropped():
    out = reduce_dom(fixture("jsonld.html"))
    assert "<script" not in out
    assert "application/ld+json" not in out  # already harvested deterministically


def test_hard_cap_is_honoured_on_a_huge_page():
    huge = "<html><body>" + "<div class='x'><p>" + ("word " * 40) + "</p></div>" * 5000 + "</body></html>"
    assert len(reduce_dom(huge)) <= CAP


def test_attributes_are_filtered_and_urls_truncated():
    html = (
        '<html><body><a href="https://example.com/' + "y" * 300 + '" '
        'onclick="steal()" class="keep" data-junk="drop">text</a></body></html>'
    )
    out = reduce_dom(html)
    assert "onclick" not in out and "data-junk" not in out
    assert 'class="keep"' in out
    assert "y" * 300 not in out
