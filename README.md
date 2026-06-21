# SAPHIR Pro / CALCMO

Application bureau professionnelle pour calculer la MGA et la Moyenne d'Orientation BEPC.

Le lanceur actif reproduit exactement l'interface HTML fournie dans `CALCUL_MOYENNE_ORIENTATION.html`, dans une fenetre Python native via WebView.

## Lancer le logiciel

```powershell
python run.py
```

## Modules

- **Calcul MGA** : saisie des moyennes trimestrielles et calcul instantane de la MGA comme dans le HTML fourni.
- **Calcul MO (BEPC)** : transfert des MGA, saisie des notes BEPC et calcul automatique de la MO.
- **Historique** : sauvegarde locale des calculs manuels enregistres.

Le fichier reproduit est ici :

```text
web\CALCUL_MOYENNE_ORIENTATION.html
```

## Fichier Excel attendu

Le fichier source doit contenir les colonnes suivantes :

`NOM`, `CLASSE`, `ETABLISSEMENT`, `REDACTION_T1`, `REDACTION_T2`, `REDACTION_T3`, `MATHS_T1`, `MATHS_T2`, `MATHS_T3`, `PC_T1`, `PC_T2`, `PC_T3`, `ANGLAIS_T1`, `ANGLAIS_T2`, `ANGLAIS_T3`, `REDACTION_BEPC`, `MATHS_BEPC`, `PC_BEPC`, `ANGLAIS_ECRIT`, `ANGLAIS_ORAL`.

Depuis l'application, le bouton **Modele Excel** cree un classeur de saisie pret a remplir.

## Formules

- MGA : `(T1 + 2*T2 + 2*T3) / 5`
- Anglais BEPC : `(Ecrit + Oral) / 2`
- MO : `Somme[(MGA + Note BEPC) * coeff] / (2 * Somme coeff)`

## Construire un executable Windows

```powershell
.\build_exe.bat
```

L'executable sera genere dans `dist\CALCMO-Pro.exe` si PyInstaller est installe.
