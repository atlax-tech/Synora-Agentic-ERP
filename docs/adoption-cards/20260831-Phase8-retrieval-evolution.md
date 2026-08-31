# Phase 8 Retrieval Evolution Adoption Card

状态：`EVALUATED / NOT ADOPTED PENDING USER DECISION`

日期：2026-08-31

## 范围

本卡只记录 T05/P8.3 的固定开发评测。四个 arm 使用同一份 T04
`p8-retrieval-baseline.json`、同一批确定性 heading-aware chunks、同一组
permission/source/revision/ERP-version filters 和 top-k 契约。实验代码位于
Runtime 的 LAB_ONLY evaluation/retrieval 模块，不改变 Coach、Provider、Gateway、
ERP 工具、Memory 权威或业务默认路径。

模型只在本机 CPU 推理，未使用远程 inference API，模型权重和缓存未进入 Git：

- embedding：`intfloat/multilingual-e5-small`
- embedding revision：`614241f622f53c4eeff9890bdc4f31cfecc418b3`
- reranker：`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`
- reranker revision：`1427fd652930e4ba29e8149678df786c240d8825`
- Python：`3.14.5`
- optional dependencies：`sentence-transformers==6.0.1`、`torch==2.13.0`、`socksio==1.0.0`

## Evidence table

固定数据集共 9 cases，其中 5 个正例、4 个负例。指标只对正例计算；负例使用
T05 显式策略：`wrong-permission` 要求禁止的 `public.md` 不出现且所有返回仍在
请求 scope，wrong revision/ERP version/unrelated 要求严格无命中。

| Metric | FTS5 | Vector | Hybrid | Rerank |
| --- | ---: | ---: | ---: | ---: |
| hit@5 | 1.000 | 1.000 | 1.000 | 1.000 |
| recall@5 | 1.000 | 1.000 | 1.000 | 1.000 |
| MRR@5 | 1.000 | 1.000 | 1.000 | 1.000 |
| negative-case correctness | 1.000 | 1.000 | 1.000 | 1.000 |
| metadata boundary violations | 0 | 0 | 0 | 0 |
| injection boundary violations | 0 | 0 | 0 | 0 |
| median query latency (ms) | 0.052 | 6.842 | 6.610 | 18.061 |
| p95 query latency (ms, n=9) | 0.106 | 9.348 | 8.538 | 208.897 |
| build latency (ms) | 0.149 | 13242.644 | 13242.793 | 17791.386 |
| peak process memory (bytes) | 278528 | 976257024 | 976322560 | 1512669184 |
| index size (bytes) | 0 | 27648 | 27648 | 27648 |
| model size (bytes) | 0 | 986585656 | 986585656 | 985491948 |

All four arms completed `PASS`; the report's `all_safety_passed` is `true`. Running
the complete comparison twice produced the same report fingerprint:

`833804474509516335fcd7394750e7fa736f142cd48d6984d3f12c38d19855c3`

Arm fingerprints were stable as well:

- FTS5: `ecf4e4604df49fc42e812ccbe006ec57a71ede55f0b07697a18c29bf8c42348a`
- Vector: `3785698402c9253ac1db804c41a961a63ac3cba8fcd558915a8f708cd673fdf2`
- Hybrid: `3785698402c9253ac1db804c41a961a63ac3cba8fcd558915a8f708cd673fdf2`
- Rerank: `c5a7e988f8d5c0e6b34b333f96f20520cabab0e3190c0a5a2512a6ce90ac069f`

## Boundary evidence

- `wrong-permission`: FTS5 returned no hits. Vector returned only authorized
  `cjk.md`, `normal.md`, and `poisoned.md`; `public.md` was absent. Hybrid and
  Rerank also kept `public.md` absent. These authorized semantic alternatives are
  recorded rather than mislabeled as a lexical no-hit.
- `wrong-revision`: all arms returned zero hits.
- `wrong-erp-version`: all arms returned zero hits.
- `unrelated`: all arms returned zero hits.
- `retrieval-injection`: the poisoned text remained `UNTRUSTED` reference data;
  ContextBuilder did not add tools or alter the system message.
- Every returned hit retained the original chunk ID, content digest, source
  revision, ERP version, permission scope and citation metadata.

## Decision

`KEEP_FTS5`. The alternatives are measured and remain `LAB_ONLY`; none is adopted
into the business path. The fixed development set shows no quality improvement over
FTS5, while local model loading adds roughly 0.99 GB model storage, up to 1.51 GB
observed process memory and materially higher latency. This is not a production
superiority claim.

If a later user-approved adoption is justified by a larger representative corpus,
the business retrieval contract, ContextBuilder integration, operational model
distribution and rollback evidence would need a new decision and review. This card
does not authorize any of those changes.
