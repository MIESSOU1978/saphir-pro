# SAPHIR Pro — Version Android

## Prérequis

1. **Node.js** (déjà installé — v25.8.1)
2. **Java JDK 17+** — Télécharger depuis https://adoptium.net/
3. **Android Studio** — Télécharger depuis https://developer.android.com/studio
   - Installer l'Android SDK (API 34 minimum)
   - Installer Build Tools 34.0.0

## Variables d'environnement

Après installation, ajouter au PATH :
```
JAVA_HOME = C:\Program Files\Eclipse Adoptium\jdk-17.x.x
ANDROID_HOME = C:\Users\PC\AppData\Local\Android\Sdk
```

## Build

```bash
cd android-build
npm install
npx cap add android
npx cap sync android
npx cap open android
```

Dans Android Studio :
1. Attendre le sync Gradle
2. Menu **Build > Build Bundle(s) / APK(s) > Build APK(s)**
3. Le .apk sera dans `android/app/build/outputs/apk/debug/app-debug.apk`

## Pour publier sur le Play Store

```bash
npx cap build android
```
Ou via Android Studio : **Build > Generate Signed Bundle / APK**

## Structure

```
android-build/
├── www/
│   ├── index.html       (UI — copie du HTML original)
│   ├── api.js           (intercepteur fetch → SQLite)
│   ├── db.js            (module SQLite Capacitor)
│   └── manifest.json    (PWA manifest)
├── android/             (généré par Capacitor)
├── capacitor.config.ts
├── package.json
└── README.md
```
