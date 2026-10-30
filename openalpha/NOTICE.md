# Notices

OpenAlpha borrows ideas and a small amount of test data from
**trader-skill** by Jake Schincariol (https://github.com/Jakeschincariol/trader-skill,
commit 5189e79), used under the MIT License:

    MIT License
    Copyright (c) 2026 Jake Schincariol

    Permission is hereby granted, free of charge, to any person obtaining a copy of this
    software and associated documentation files (the "Software"), to deal in the Software
    without restriction, including without limitation the rights to use, copy, modify, merge,
    publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons
    to whom the Software is furnished to do so, subject to the following conditions: the above
    copyright notice and this permission notice shall be included in all copies or substantial
    portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

What was taken:

- The method of building quarterly figures from SEC XBRL company facts (quarters from 10-Qs,
  the fourth quarter derived as the 10-K year minus the three quarters, marked as derived).
  Re-implemented here, not copied.
- The SEC filing item labels approach for 8-K monitoring and the Yahoo RSS headline source.
  Re-implemented here, not copied.
- The NYSE holiday and early-close list for 2026 and 2027 (the repo cites nyse.com).
- Test fixtures in `backend/tests/fixtures/trader_skill/`: real SEC company facts and filing
  lists for Apple, and a Yahoo Finance RSS excerpt, copied unchanged.
