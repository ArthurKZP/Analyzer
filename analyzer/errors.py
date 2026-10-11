"""Une demande qui ne désigne rien (une page, un joueur, une main, une famille de spots… inconnus) : NotFound, que le
serveur de l'application traduit en « introuvable » (404). Toute autre erreur, même une KeyError levée pendant un
calcul, est une erreur d'analyse (500, le détail dans le terminal) : elle ne se cache pas derrière une page
introuvable."""


class NotFound(KeyError):
    """Ce que la demande désigne n'existe pas."""
