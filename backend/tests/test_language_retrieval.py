from app.language.retrieval import language_query_variants
from app.retrieval.advanced import query_variants


def test_romanized_domain_forms_are_additive_and_bounded():
    original = "yojana S1 varshik aay seema 200000 APP-001"
    variants, trace = language_query_variants(original)
    assert len(variants) == 1 and variants[0].startswith(original)
    assert "योजना" in variants[0] and "income" in variants[0]
    assert "APP-001" in variants[0] and "200000" in variants[0]
    assert trace["original_preserved"]
    assert query_variants(original)[0][:2] == [original, variants[0]]


def test_native_numerals_and_names_remain_original():
    original = 'अनुदान सीमा २५००० आईडी-A००१ "Seema"'
    variants, _ = language_query_variants(original)
    assert variants[0].startswith(original)
    assert "25000" in variants[0]
    assert "आईडी-A००१" in variants[0]


def test_ambiguity_and_substrings_do_not_invent_meaning():
    assert language_query_variants("kal Ram ID-yojana-001")[0] == []
    assert language_query_variants("aayush APP-001")[0] == []
    assert language_query_variants("yojana " + "x" * 1993)[0] == []
