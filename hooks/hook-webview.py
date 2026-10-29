"""Package only the Windows WebView2 backend; do not import other GUI toolkits."""
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = collect_data_files("webview")
binaries = collect_dynamic_libs("webview")
hiddenimports = ["webview.platforms.winforms", "webview.platforms.edgechromium",
                 "webview.platforms.mshtml"]
