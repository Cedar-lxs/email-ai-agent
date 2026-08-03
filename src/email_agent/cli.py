"""命令行入口。"""
import json
import sys

from email_agent.application.email_service import EmailAgent, usage
from email_agent.application.evaluation_service import RetrievalEvaluator
from email_agent.domain.models import RetrievalQuery
from email_agent.paths import get_project_paths


def main(args=None) -> int:
    agent = EmailAgent()
    args = list(sys.argv[1:] if args is None else args)
    command = args[0] if args else "once"
    try:
        if command == "once": agent.run_once()
        elif command == "forever": agent.run_forever()
        elif command == "test": agent.test_connection()
        elif command == "drafts": agent.list_drafts()
        elif command == "review" and len(args) == 2: agent.review_draft(args[1])
        elif command == "edit" and len(args) == 3: agent.edit_draft(args[1], args[2])
        elif command == "approve" and len(args) == 2: agent.approve_draft(args[1])
        elif command == "reject" and len(args) >= 2: agent.reject_draft(args[1], " ".join(args[2:]) or "人工拒绝")
        elif command == "stats": agent.show_stats()
        elif command == "install-task": agent.install_task()
        elif command == "remove-task": agent.remove_task()
        elif command == "install-web-task": agent.install_web_task()
        elif command == "remove-web-task": agent.remove_web_task()
        elif command == "rag-build":
            stats = agent.retriever.rebuild()
            print(json.dumps({"entries": stats.entries, "sources": stats.sources,
                              "errors": stats.errors,
                              "index": agent.retriever.get_stats()}, ensure_ascii=False, indent=2))
        elif command == "rag-search" and len(args) >= 2:
            hits = agent.retriever.retrieve(RetrievalQuery(" ".join(args[1:])), agent.top_k)
            for hit in hits:
                print(f"{hit.score:.3f} | {hit.source} | {hit.section}\n{hit.content[:300]}\n")
        elif command == "rag-eval":
            evaluator = RetrievalEvaluator(agent.retriever)
            path = get_project_paths().data / "rag_evaluation.jsonl"
            cases = (evaluator.load_cases(path) if path.is_file()
                     else evaluator.save_cases(path, 100))
            print(json.dumps(evaluator.evaluate(cases), ensure_ascii=False, indent=2))
        else: usage()
        return 0
    except Exception as exc:
        print(f"操作失败: {exc}")
        return 1
