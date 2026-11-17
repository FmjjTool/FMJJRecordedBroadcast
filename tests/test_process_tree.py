import sys
import unittest

from toolbox import process_tree


class ProcessTreeTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows Job Object only")
    def test_create_and_terminate_job(self):
        job = process_tree.create_kill_job()
        self.assertTrue(job)
        self.assertTrue(process_tree.terminate_job(job))

    def test_assign_job_returns_false_for_invalid_process(self):
        self.assertFalse(process_tree.assign_job(None, object()))


if __name__ == "__main__":
    unittest.main()
