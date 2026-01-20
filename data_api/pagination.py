from rest_framework.pagination import PageNumberPagination


class StandardResultsSetPagination(PageNumberPagination):
    """
    Pagination standard pour les listes de données tabulaires.
    """
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500
