# Design QA - Identité visuelle SAPHIR Pro

- Source visual truth: `C:\Users\PC\Documents\CALCMO\tmp\pdfs\bulletin-reference-page-1.png`
- Implementation screenshot: `C:\Users\PC\Documents\CALCMO\qa-bulletin-identity-mo.png`
- Comparison evidence: `C:\Users\PC\Documents\CALCMO\qa-bulletin-identity-comparison.png`
- Viewport: 1280 x 800 (application), 1600 x 1000 (comparaison)
- State: écran MO renseigné avec les mêmes données et résultats que le bulletin source

**Full-view comparison evidence**

La comparaison réunit le bulletin et l'application dans une même vue. L'application reprend le vert institutionnel, le fond ivoire, l'accent or, le bandeau tricolore, Ebrima, les tableaux structurés et la synthèse clair/vert du document source.

**Focused region comparison evidence**

Une capture native dédiée de l'écran MO a été contrôlée à 1280 px. Les champs, le tableau à six colonnes, les coefficients, le total `140,000` et la MO `11,6667` restent nettement lisibles ; aucun recadrage supplémentaire n'était nécessaire.

**Findings**

- Aucun écart P0, P1 ou P2 restant.
- Typographie : Ebrima, poids, capitales, interlignage et hiérarchie cohérents avec le bulletin.
- Espacement et mise en page : marges, cadres fins, rayons de 6 à 8 px et densité administrative équilibrés.
- Couleurs : palette vert `#0b4a35`, or `#f2b632`, ivoire et gris-vert conforme à la source, avec contraste lisible.
- Images et icônes : aucune image illustrative n'est requise dans l'interface ; le petit pictogramme académique du bulletin n'est pas remplacé par une approximation.
- Copie : les libellés métier et les parcours MGA/MO restent inchangés et cohérents.
- Responsive : à 390 px, aucun débordement de page ; le tableau dense défile dans son propre cadre et les résultats s'empilent.

**Patches made since the previous QA pass**

- Remplacement complet du thème sombre par l'identité institutionnelle du bulletin.
- Nouvel en-tête République / SAPHIR Pro et navigation numérotée.
- Harmonisation des cartes, formulaires, tableaux, coefficients, boutons, résultats et historique.
- Suppression des emojis et des effets lumineux.
- Correction du débordement mobile des champs et du tableau MO.

**Implementation checklist**

- [x] Écran d'entrée harmonisé
- [x] Écran MGA harmonisé
- [x] Écran MO harmonisé et calcul vérifié
- [x] Historique et actions harmonisés
- [x] Bureau et mobile contrôlés
- [x] Bulletin imprimable préservé

**Follow-up polish**

- P3 acceptable : sur téléphone, le tableau à six colonnes nécessite un défilement horizontal interne afin de préserver la lisibilité des valeurs.

final result: passed
