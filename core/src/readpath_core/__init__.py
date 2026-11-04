# SPDX-License-Identifier: Apache-2.0
"""Readpath core: check, score and fix PDF accessibility.

Typical use::

    from readpath_core import check_file, autofix_file

    report = check_file("minutes.pdf")
    print(report.score, report.grade)
    autofix_file("minutes.pdf", "minutes-accessible.pdf")

For the guided review (reading order, alt text, table headers) open the file
with pikepdf and use :class:`readpath_core.review.Review`.
"""

from .checks import Check, ProblemOpening, Report, check_file, compute_grade, compute_score, run_checks
from .fixer import FixResult, autofix_file, autofix_pdf, finalize_pdf
from .review import Review, ReviewError
from .tagger import TagResult, autotag, is_tagged

__version__ = "0.1.0"

__all__ = [
    "Check", "FixResult", "ProblemOpening", "Report", "Review", "ReviewError", "TagResult",
    "autofix_file", "autofix_pdf", "finalize_pdf", "autotag", "check_file", "compute_grade", "compute_score",
    "is_tagged", "run_checks", "__version__",
]
