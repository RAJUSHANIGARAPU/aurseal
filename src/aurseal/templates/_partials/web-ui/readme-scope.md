**In scope:** _(the user journeys this suite covers on {{ target_url }})_

**Out of scope**, and the risk each leaves:

- Browsers other than Chromium: _(rendering differences in Firefox and WebKit go undetected)_
- Visual regression: _(layout breaks that keep the DOM intact go undetected)_
- Performance and load: _(slow pages only fail when they exceed the assertion timeout)_
