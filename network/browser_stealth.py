from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineUrlRequestInterceptor

class PersistentWebView(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.force_close = False 

    def closeEvent(self, event: QCloseEvent):
        if self.force_close:
            event.accept()
        else:
            self.move(-10000, -10000)
            self.load(QUrl("about:blank"))
            event.ignore()

class StealthWebPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        if any(x in message for x in ["TypeError", "JQMIGRATE", "Permissions-Policy", "WebGPU", "xr-spatial-tracking", "audio/mp4"]):
            return
        pass

    def createWindow(self, _type):
        print("[AdBlock] 🛡️ Ghigliottina su tentativo di apertura Pop-up/Pop-under!")
        return None  # Restituire None blocca fisicamente l'apertura della nuova finestra

    def acceptNavigationRequest(self, url, _type, isMainFrame):
        url_str = url.toString().lower()
        host = url.host().lower()

        universal_trash_roots = [
            'exoclick', 'adsterra', 'propellerads', 'popunder', 'popads', 
            'adsystem', 'adserver', 'adcash', 'hilltopads', 'trafficjunky',
            'chaturbate', 'stripchat', 'bongacams', 'jasmin', 'jerkmate', 
            'livejasmin', 'pornhub', 'xvideos', 'xnxx', 'spankbang', 'eporner',
            '1xbet', 'betting', 'casino', 'doubleclick', 'googleadservices',
            'taboola', 'outbrain', 'mgid', 'traffic', 'tracker', 'banner', 
            'analytics', 'poker', 'roulette', 'gamble', 'slots', 'sportsbook',
            'porn', 'xxx', 'sex', 'cams', 'nude', 'adult', 'dating', 'tube', 'fetish'
        ]
        
        if any(root in host for root in universal_trash_roots):
            print(f"[AdBlock] 🛡️ Blocco Euristico (Parola chiave sospetta): {host}")
            return False

        if _type == QWebEnginePage.NavigationType.NavigationTypeRedirect:
            safe_domains = ['steamrip', 'megadb', 'buzzheavier', 'bzzhr', '1fichier', 'gofile', 'qiwi', 'pixeldrain', 'fileditch', 'datanodes', 'bowfile', 'clicknupload', 'ddownload', 'filecrypt', 'mediafire', 'google', 'gamedrive', 'krakenfiles']
            
            if not any(safe in host for safe in safe_domains):
                print(f"[AdBlock] 🛡️ Blocco Comportamentale (Redirect non richiesto): {host}")
                return False

        return super().acceptNavigationRequest(url, _type, isMainFrame)

class AdBlockInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bad_domains = [
            'exoclick', 'adsterra', 'propellerads', 'popunder', 'popads', 
            'adsystem', 'adserver', 'adcash', 'hilltopads', 'trafficjunky',
            'chaturbate', 'stripchat', 'bongacams', 'jasmin', 'jerkmate', 
            'livejasmin', 'pornhub', 'xvideos', 'xnxx', 'spankbang', 'eporner',
            '1xbet', 'betting', 'casino', 'doubleclick', 'googleadservices',
            'taboola', 'outbrain', 'mgid', 'traffic', 'tracker', 'banner', 
            'analytics', 'poker', 'roulette', 'gamble', 'slots', 'sportsbook',
            'porn', 'xxx', 'sex', 'cams', 'nude', 'adult', 'dating', 'tube', 'fetish'
        ]

    def interceptRequest(self, info):
        url = info.requestUrl().toString().lower()
        host = info.requestUrl().host().lower()
        
        for bad_word in self.bad_domains:
            if bad_word in host or bad_word in url:
                info.block(True)
                return
