import urllib.request, json

# Read the HTML
with open(r"C:\Users\PC\Documents\sauvegarde 2\CALCMO\android-build\www\index.html", "r", encoding="utf-8") as f:
    html = f.read()

# The Android interceptor (non-module, runs before main script)
interceptor = r"""
<script>
/* ══ ANDROID API INTERCEPTOR ══ */
(function(){
  var STORE_KEY='saphirpro_eleves';
  var _nextId=1;
  function loadAll(){try{var r=localStorage.getItem(STORE_KEY);return r?JSON.parse(r):[];}catch(e){return[];}}
  function saveAll(l){localStorage.setItem(STORE_KEY,JSON.stringify(l));}
  var all=loadAll();if(all.length)_nextId=Math.max.apply(null,all.map(function(e){return e.id;}))+1;

  var origFetch=window.fetch;
  window.fetch=function(url,opts){
    opts=opts||{};
    var method=(opts.method||'GET').toUpperCase();
    var path=typeof url==='string'?url:(url.url||'');
    if(!path.startsWith('/api/'))return origFetch(url,opts);

    return new Promise(function(resolve){
      try{
        if(path==='/api/eleves'&&method==='GET'){
          var d=loadAll();d.sort(function(a,b){return b.id-a.id;});
          resolve(jsonResp(d));return;
        }
        if(path==='/api/eleves'&&method==='POST'){
          var body=JSON.parse(opts.body||'{}');
          var all2=loadAll();var id=_nextId++;
          var now=new Date().toISOString().slice(0,10);
          var eleve={id:id,nom:body.nom||'',matricule:body.matricule||'',classe:body.classe||'',etablissement:body.etablissement||'',annee:body.annee||'',created_at:now};
          var resultat={eleve_id:id,total:body.total||0,mo:body.mo||0,mention:body.mention||'',matieres:body.matieres||{},date_calc:now};
          all2.push(Object.assign({},eleve,resultat));
          saveAll(all2);
          resolve(jsonResp({eleve:eleve,resultat:resultat}));return;
        }
        if(path==='/api/eleves/clear'&&method==='DELETE'){
          localStorage.removeItem(STORE_KEY);_nextId=1;
          resolve(jsonResp({ok:true}));return;
        }
        if(path==='/api/eleves/delete-multiple'&&method==='POST'){
          var b=JSON.parse(opts.body||'{}');var ids=b.ids||[];
          var all3=loadAll();var idSet={};ids.forEach(function(i){idSet[i]=true;});
          all3=all3.filter(function(e){return !idSet[e.id];});
          saveAll(all3);
          resolve(jsonResp({ok:true,deleted:ids.length}));return;
        }
        var idMatch=path.match(/^\/api\/eleves\/(\d+)$/);
        if(idMatch&&method==='GET'){
          var eid=parseInt(idMatch[1]);var all4=loadAll();
          var found=null;for(var i=0;i<all4.length;i++){if(all4[i].id===eid){found=all4[i];break;}}
          if(!found){resolve(jsonErr('Not found',404));return;}
          resolve(jsonResp(found));return;
        }
        if(idMatch&&method==='DELETE'){
          var did=parseInt(idMatch[1]);var all5=loadAll();
          all5=all5.filter(function(e){return e.id!==did;});
          saveAll(all5);
          resolve(jsonResp({ok:true}));return;
        }
        if(method==='PUT'){
          var puMatch=path.match(/^\/api\/eleves\/(\d+)$/);
          if(puMatch){
            var pid=parseInt(puMatch[1]);var pb=JSON.parse(opts.body||'{}');
            var all6=loadAll();var now2=new Date().toISOString().slice(0,10);
            for(var j=0;j<all6.length;j++){
              if(all6[j].id===pid){
                all6[j]=Object.assign({},all6[j],{nom:pb.nom||'',matricule:pb.matricule||'',classe:pb.classe||'',etablissement:pb.etablissement||'',annee:pb.annee||'',total:pb.total||0,mo:pb.mo||0,mention:pb.mention||'',matieres:pb.matieres||{},date_calc:now2});
                saveAll(all6);resolve(jsonResp(all6[j]));return;
              }
            }
            resolve(jsonErr('Not found',404));return;
          }
        }
        if(method==='POST'&&path.match(/\/duplicate$/)){
          var duMatch=path.match(/^\/api\/eleves\/(\d+)/);
          if(duMatch){
            var srcId=parseInt(duMatch[1]);var all7=loadAll();var src=null;
            for(var k=0;k<all7.length;k++){if(all7[k].id===srcId){src=all7[k];break;}}
            if(!src){resolve(jsonErr('Not found',404));return;}
            var id2=_nextId++;var now3=new Date().toISOString().slice(0,10);
            var ne={id:id2,nom:src.nom,matricule:src.matricule,classe:src.classe,etablissement:src.etablissement,annee:src.annee,created_at:now3};
            var nr={eleve_id:id2,total:src.total,mo:src.mo,mention:src.mention,matieres:src.matieres||{},date_calc:now3};
            all7.push(Object.assign({},ne,nr));saveAll(all7);
            resolve(jsonResp({eleve:ne,resultat:nr},201));return;
          }
        }
        resolve(jsonErr('Not found',404));
      }catch(err){
        console.error('[API Error]',err);
        resolve(jsonErr(err.message||'Server error',500));
      }
    });
  };
  function jsonResp(d,s){return new Response(JSON.stringify(d),{status:s||200,headers:{'Content-Type':'application/json'}});}
  function jsonErr(m,s){return new Response(JSON.stringify({error:m}),{status:s,headers:{'Content-Type':'application/json'}});}
  console.log('[SAPHIR Pro] API intercepteur Android actif');
})();
</script>
"""

# Insert before the main <script> tag
html = html.replace("\n<script>\n/* ── MATIÈRES ── */", interceptor + "\n<script>\n/* ── MATIÈRES ── */")

with open(r"C:\Users\PC\Documents\sauvegarde 2\CALCMO\android-build\www\index.html", "w", encoding="utf-8") as f:
    f.write(html)

print("OK - interceptor injected")
