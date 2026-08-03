"""无需外部模型的 CPU 词项覆盖重排器，并兼容可选 Cross-Encoder。"""
import re


class TokenOverlapReranker:
    @staticmethod
    def _tokens(text: str) -> set[str]:
        text = text.lower()
        tokens = set(re.findall(r"[a-z][a-z0-9]*(?:[-_/\.][a-z0-9]+)*|\d+(?:\.\d+)?", text))
        for sequence in re.findall(r"[\u4e00-\u9fff]+", text):
            tokens.update(sequence[index:index + 2] for index in range(max(0, len(sequence) - 1)))
            tokens.update(sequence[index:index + 3] for index in range(max(0, len(sequence) - 2)))
        return tokens

    def rerank(self, query: str, documents: list[str]):
        query_tokens = self._tokens(query)
        results = []
        for index, document in enumerate(documents):
            document_tokens = self._tokens(document)
            score = len(query_tokens & document_tokens) / max(1, len(query_tokens))
            results.append({"index": index, "score": score})
        return sorted(results, key=lambda item: item["score"], reverse=True)
