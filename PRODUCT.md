# KeyTrail · 钥迹

Windows x64 local application for a user coordinating independent computers on the public #71 puzzle. Main tasks: run/stop, choose a bounded range, resume saved work, configure a receiving address, inspect verified payout receipts. UI defaults to English and supports five saved languages.

Preserve all existing checkpoints and schema-1 plans. Never start searching or arm automatic payout on an update. Automatic payout is an explicit per-process user authorization, bound to the displayed receiving address, with a 200,000 sat fee ceiling. No real mainnet transaction is part of development acceptance.

Receiving addresses are local-only, empty on new installs, never compiled into distributable builds. The embedded frameless WebView2 shell replaces the extra system title bar; window controls live in the graphite header. Close hides the UI while backend work remains available through the tray.
