# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproducible synthetic SQLite corpus, NOT RevisionWorx application performance."""
from __future__ import annotations
import argparse
import json
import sqlite3
import time
from pathlib import Path

def generate(path: Path, projects: int, documents: int, edges: int, batch: int = 10000) -> dict:
    if projects < 1 or documents < 2 or not 0 <= edges <= documents * (documents - 1):
        raise ValueError("invalid cardinalities")
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    db = sqlite3.connect(path)
    try:
        db.executescript("""
          CREATE TABLE projects(id INTEGER PRIMARY KEY, number TEXT NOT NULL UNIQUE, name TEXT NOT NULL);
          CREATE TABLE documents(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, part_number TEXT NOT NULL, path TEXT NOT NULL);
          CREATE TABLE dependencies(parent_id INTEGER NOT NULL, child_id INTEGER NOT NULL, revision TEXT NOT NULL,
                                    PRIMARY KEY(parent_id,child_id,revision));
          CREATE INDEX docs_part ON documents(part_number);
          CREATE INDEX docs_project ON documents(project_id);
          CREATE INDEX deps_child ON dependencies(child_id);
        """)
        for j in range(0, projects, batch):
            db.executemany("INSERT INTO projects VALUES(?,?,?)",
                ((i+1, f"P-{i+1:07d}", f"Project {i+1}") for i in range(j, min(j+batch,projects))))
            db.commit()
        for j in range(0, documents, batch):
            db.executemany("INSERT INTO documents VALUES(?,?,?,?)",
                ((i+1, i%projects+1, f"PN-{i+1:09d}", f"Models/part-{i+1:09d}.ipt")
                 for i in range(j, min(j+batch,documents))))
            db.commit()
        for j in range(0, edges, batch):
            rows=[]
            for i in range(j,min(j+batch,edges)):
                parent=i%documents+1
                offset=i//documents+1
                child=(parent-1+offset)%documents+1
                rows.append((parent,child,"A"))
            db.executemany("INSERT INTO dependencies VALUES(?,?,?)",rows)
            db.commit()
        actual={"projects":db.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
                "documents":db.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "edges":db.execute("SELECT COUNT(*) FROM dependencies").fetchone()[0]}
        if actual!={"projects":projects,"documents":documents,"edges":edges}:
            raise AssertionError(f"cardinality mismatch: {actual}")
        return {"kind":"synthetic-sqlite-generation","application_benchmark":False,
                "counts":actual,"generation_seconds":round(time.perf_counter()-start,3),
                "bytes":path.stat().st_size}
    finally:
        db.close()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--projects",type=int,default=10000)
    parser.add_argument("--documents",type=int,default=2000000)
    parser.add_argument("--edges",type=int,default=500000)
    args=parser.parse_args()
    print(json.dumps(generate(args.out,args.projects,args.documents,args.edges),indent=2))

if __name__=="__main__":
    main()
