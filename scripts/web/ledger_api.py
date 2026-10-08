# -*- coding: utf-8 -*-
"""LedgerService 위임 믹스인(SRP: 대장·문서·Q&A 조회 API)."""

def _ledger_service():
    import web_server as ws
    return ws.get_ledger_service()


class LedgerApiMixin:
    # Delegated methods to LedgerService for backwards compatibility with tests and callers
    def _get_service(self=None, *args, **kwargs):
        """Get a cached LedgerService instance. Works even when self is None (class-level calls from tests)."""
        return _ledger_service()

    def get_stats(self=None, *args, **kwargs):
        return _ledger_service().get_stats()

    def query_ledger(self, year=None, kw=None, status=None, due=None, *args, **kwargs):
        return _ledger_service().query_ledger(year=year, kw=kw, status=status, due=due)

    def query_documents(self, year=None, kw=None, slim=False, *args, **kwargs):
        return _ledger_service().query_documents(year=year, kw=kw, slim=slim)

    def query_qa_items(self, year=None, kw=None, slim=False, *args, **kwargs):
        return _ledger_service().query_qa_items(year=year, kw=kw, slim=slim)

    def search_all(self, kw='', year=None, search_type='all', slim=False,
                   use_synonyms=True, with_snippet=False, *args, **kwargs):
        return _ledger_service().search_all(
            kw=kw, year=year, search_type=search_type, slim=slim,
            use_synonyms=use_synonyms, with_snippet=with_snippet,
        )

    def suggest(self, prefix='', limit=10, *args, **kwargs):
        return _ledger_service().suggest(prefix, limit=limit)

    def search_insights(self, limit=20, days=90, *args, **kwargs):
        return _ledger_service().search_insights(limit=limit, days=days)

    def get_ledger_item(self, ledger_id='', *args, **kwargs):
        return _ledger_service().get_ledger_item(ledger_id)

    def insert_ledger_item(self, data=None, *args, **kwargs):
        return _ledger_service().insert_ledger_item(data)

    def update_ledger_item(self, ledger_id='', data=None, *args, **kwargs):
        return _ledger_service().update_ledger_item(ledger_id, data)

    def delete_ledger_item(self, ledger_id='', *args, **kwargs):
        return _ledger_service().delete_ledger_item(ledger_id)

    def restore_ledger_item(self, ledger_id='', *args, **kwargs):
        return _ledger_service().restore_ledger_item(ledger_id)

    def get_ledger_history(self, ledger_id=None, limit=100, *args, **kwargs):
        return _ledger_service().get_ledger_history(ledger_id=ledger_id, limit=limit)
