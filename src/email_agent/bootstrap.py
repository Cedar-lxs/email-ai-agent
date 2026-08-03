"""应用依赖组装。"""
from email_agent.application.email_service import EmailAgent
from email_agent.application.knowledge_service import KnowledgeService


def create_services(config_path=None):
    agent = EmailAgent(config_path)
    knowledge = KnowledgeService(agent.retriever.knowledge_dir, agent.retriever)
    return agent, agent.review, knowledge
