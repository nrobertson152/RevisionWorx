"""Local independent-process fencing tests. Not cross-host certification."""
import multiprocessing as mp
import tempfile
import unittest
from pathlib import Path
from prototype.authority import Authority, Fenced


def contender(path, gate, queue, name):
    a=Authority(path)
    gate.wait()
    try:
        queue.put(("won",name,a.acquire("drawing",name)))
    except Fenced:
        queue.put(("lost",name,None))


class AuthorityTests(unittest.TestCase):
    def test_revoked_active_writer_cannot_publish(self):
        with tempfile.TemporaryDirectory() as td:
            a=Authority(Path(td)/"authority.db")
            g=a.acquire("drawing","alice")
            a.override("drawing","operator recovery","unverified-local-actor")
            with self.assertRaises(Fenced):
                a.publish("drawing","alice",g,"tx-old","old")
            fresh=a.acquire("drawing","bob")
            self.assertGreater(fresh,g)
            self.assertEqual(a.publish("drawing","bob",fresh,"tx-new","new"),"committed")

    def test_idempotency(self):
        with tempfile.TemporaryDirectory() as td:
            a=Authority(Path(td)/"authority.db")
            g=a.acquire("drawing","alice")
            self.assertEqual(a.publish("drawing","alice",g,"tx1","data"),"committed")
            self.assertEqual(a.publish("drawing","alice",g,"tx1","data"),"already-committed")
            with self.assertRaises(Fenced):
                a.publish("drawing","alice",g,"tx1","changed")

    def test_process_contention(self):
        with tempfile.TemporaryDirectory() as td:
            path=str(Path(td)/"authority.db")
            Authority(path)
            ctx=mp.get_context("spawn")
            gate=ctx.Event()
            q=ctx.Queue()
            procs=[ctx.Process(target=contender,args=(path,gate,q,str(i))) for i in range(6)]
            for p in procs:p.start()
            gate.set()
            results=[q.get(timeout=20) for _ in procs]
            for p in procs:
                p.join(timeout=20)
                self.assertEqual(p.exitcode,0)
            self.assertEqual(sum(x[0]=="won" for x in results),1)


if __name__=="__main__":
    unittest.main()
