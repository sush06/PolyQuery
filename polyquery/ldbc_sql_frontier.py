"""SQL Server level-order search with a request-local visited set."""
from .ldbc_sql_graph import LDBCSQLGraph


class LDBCSQLFrontier(LDBCSQLGraph):
    """Keep source reads under the same projection permissions as the CTE."""

    def __init__(self, db, graph, catalog, *, timeout, driver_error):
        super().__init__(db, graph, catalog, server=True, timeout=timeout, driver_error=driver_error)

    def _paths(self, cursor, source, target, deadline):
        # A fixed-width rank sequence orders equal-distance paths exactly
        # like BFS with numerically ordered neighbors.
        sql = f"""
        SET NOCOUNT ON;
        DECLARE @source int = (SELECT rank_id FROM {self.prefix}person WHERE person_id = ?);
        DECLARE @target int = (SELECT rank_id FROM {self.prefix}person WHERE person_id = ?);
        DECLARE @seen TABLE (id int PRIMARY KEY);
        DECLARE @frontier TABLE (id int PRIMARY KEY, path varchar(max) NOT NULL);
        DECLARE @next TABLE (id int PRIMARY KEY, path varchar(max) NOT NULL);
        INSERT @seen VALUES (@source);
        INSERT @frontier VALUES (@source, RIGHT('0000000000' + CONVERT(varchar(10), @source), 10));
        WHILE EXISTS (SELECT 1 FROM @frontier)
              AND NOT EXISTS (SELECT 1 FROM @frontier WHERE id = @target)
        BEGIN
            INSERT @next (id, path)
            SELECT k.target_id,
                   MIN((f.path + RIGHT('0000000000' + CONVERT(varchar(10), k.target_id), 10))
                       COLLATE Latin1_General_100_BIN2)
            FROM @frontier f
            JOIN {self.prefix}knows k ON k.source_id = f.id
            WHERE NOT EXISTS (SELECT 1 FROM @seen s WHERE s.id = k.target_id)
            GROUP BY k.target_id;
            INSERT @seen SELECT id FROM @next;
            DELETE FROM @frontier;
            INSERT @frontier SELECT id, path FROM @next;
            DELETE FROM @next;
        END;
        SELECT path FROM @frontier WHERE id = @target;
        """
        row = cursor.execute(sql, (source, target)).fetchone()
        if row is None:
            return None
        encoded = row[0]
        ranks = [int(encoded[i:i + 10]) for i in range(0, len(encoded), 10)]
        # Chunk below SQL Server's parameter limit. Each rank is a verified
        # source ID and occurs at most once along a shortest path.
        people = {}
        for start in range(0, len(ranks), 1000):
            batch = ranks[start:start + 1000]
            marks = ",".join("?" for _ in batch)
            people.update(cursor.execute(f"SELECT rank_id,person_id FROM {self.prefix}person "
                                         f"WHERE rank_id IN ({marks})", batch).fetchall())
        return [people[rank] for rank in ranks]
