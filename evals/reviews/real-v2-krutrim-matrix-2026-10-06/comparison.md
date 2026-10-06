# Real-v2 model comparison

AI-assisted, uncalibrated review of 200 saved trials. Original automatic results remain separate.

| Model | Complete | Partial | Failed | Complete in added 20 | Original auto failed |
| --- | ---: | ---: | ---: | ---: | ---: |
| gpt-oss-120b | 29/50 | 12 | 9 | 13/20 | 19 |
| gemma-4-31b-it | 35/50 | 5 | 10 | 16/20 | 19 |
| gemma-4-26B-A4B-it | 36/50 | 9 | 5 | 15/20 | 27 |
| Qwen3.6-35B-A3B | 41/50 | 6 | 3 | 16/20 | 15 |

The primary agent checked automatic-failure/complete disagreements against saved SQL rows. Three correctly valued answers were kept partial because their retained SQL output missed the requested named single-row contract. Monetary review comments retain decimal precision. Further spot checks clarified wrong GVA growth and an omitted quotation.

Requested artifact contents remain unverified because the local application API was offline; see README.md and individual case notes. No new model calls or rescoring occurred.
