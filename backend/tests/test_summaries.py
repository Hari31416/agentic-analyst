from app.retrieval.summaries import _select_sentences, _support_records


def test_extractive_summary_sentences_keep_their_original_chunk_references() -> None:
    chunks = [
        {
            "chunk_id": "chunk-a",
            "document_id": "document-a",
            "source_id": "source-a",
            "source_version": 3,
            "document_version": 3,
            "source_name": "rules.pdf",
            "heading": "Eligibility",
            "location": {"page": 4},
            "text": (
                "Applicants must be residents of the district. Income must not exceed INR 200000. "
                "Applications close on 30 June."
            ),
            "ordinal": 8,
        },
        {
            "chunk_id": "chunk-b",
            "document_id": "document-a",
            "source_id": "source-a",
            "source_version": 3,
            "document_version": 3,
            "source_name": "rules.pdf",
            "heading": "Payment",
            "location": {"page": 9},
            "text": "Approved households receive a monthly transfer of INR 5000.",
            "ordinal": 18,
        },
    ]

    selected = _select_sentences(chunks)
    supporting = _support_records(selected, chunks)

    assert selected
    assert all(item["text"] in item["chunk"]["text"] for item in selected)
    assert {item["chunk_id"] for item in supporting} == {
        item["chunk"]["chunk_id"] for item in selected
    }
    assert all(item["source_version"] == 3 for item in supporting)
    assert all(item["location"] for item in supporting)
    assert {item["text"] for item in selected}.issubset(
        {item["excerpt"] for item in supporting}
    )


def test_support_excerpt_retains_a_selected_sentence_far_into_the_chunk() -> None:
    prefix = "Background text that is not selected. " * 50
    sentence = "The exact qualifying threshold is INR 200000 per household."
    chunk = {
        "chunk_id": "chunk-long",
        "document_id": "document-long",
        "source_id": "source-long",
        "source_version": 1,
        "document_version": 1,
        "source_name": "long.pdf",
        "heading": "Threshold",
        "location": {"page": 20},
        "text": prefix + sentence,
        "ordinal": 20,
    }
    selected = [{"text": sentence, "chunk": chunk, "terms": set()}]

    supporting = _support_records(selected, [chunk])

    assert supporting[0]["excerpt"] == sentence
    assert supporting[0]["excerpt"] in chunk["text"]
