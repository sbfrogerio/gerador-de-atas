import csv,html,re,subprocess,tempfile,time,unicodedata,urllib.parse,urllib.request
from datetime import date,datetime
from pathlib import Path
INDEX='https://www2.tjal.jus.br/cdje/index.do'; DOWNLOAD='https://www2.tjal.jus.br/cdje/downloadCaderno.do'; SEARCH='https://www2.tjal.jus.br/cpopg/search.do'
TARGET='Busca e Apreensão em Alienação Fiduciária'; cnj_re=re.compile(r'\b\d{7}-\d{2}\.\d{4}\.8\.02\.\d{4}\b')
def norm(x):
 x=unicodedata.normalize('NFKD',str(x or '')); return ' '.join(''.join(c for c in x if not unicodedata.combining(c)).lower().split())
def get(url,timeout=60):
 req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 TJAL-v0.7-class81'}); 
 with urllib.request.urlopen(req,timeout=timeout) as r:return r.read(),r.geturl()
def element(doc,id_):
 m=re.search(rf'(?is)<(?P<t>[a-z0-9]+)\b[^>]*id=["\']{re.escape(id_)}["\'][^>]*>(?P<b>.*?)</(?P=t)>',doc)
 if not m:return None
 s=re.sub(r'(?s)<[^>]+>',' ',m.group('b')); return ' '.join(html.unescape(s).replace('\xa0',' ').split()) or None
def parse_dist(v):
 if not v:return None
 m=re.search(r'(\d{2}/\d{2}/\d{4})\s+às\s+(\d{2}:\d{2})',v)
 return datetime.strptime(m.group(1)+' '+m.group(2),'%d/%m/%Y %H:%M').isoformat(timespec='minutes') if m else None
def lookup(num):
 d=re.sub(r'\D','',num); prefix=f'{d[:7]}-{d[7:9]}.{d[9:13]}'; origin=d[-4:]
 q={'conversationId':'','cbPesquisa':'NUMPROC','numeroDigitoAnoUnificado':prefix,'foroNumeroUnificado':origin,'dadosConsulta.valorConsultaNuUnificado':num,'dadosConsulta.tipoNuProcesso':'UNIFICADO'}
 try:
  payload,url=get(SEARCH+'?'+urllib.parse.urlencode(q),30); doc=payload.decode('utf-8',errors='replace'); classe=element(doc,'classeProcesso'); raw=element(doc,'dataHoraDistribuicaoProcesso')
  return {'numero':d,'numero_formatado':num,'status':'FOUND' if element(doc,'numeroProcesso') else 'OTHER','classe':classe,'distribuicao':parse_dist(raw),'distribuicao_raw':raw,'foro':element(doc,'foroProcesso'),'vara':element(doc,'varaProcesso'),'url':url}
 except Exception as e:return {'numero':d,'numero_formatado':num,'status':'ERROR','error':f'{type(e).__name__}: {e}'}
page,_=get(INDEX); doc=page.decode('utf-8',errors='replace')
pat=re.compile(r'dtPublicacao:\s*.*?value="(?P<date>\d{4}-\d{2}-\d{2})".*?cdVolume:\s*(?P<volume>\d+).*?nuDiario:\s*(?P<number>\d+)',re.S)
editions=[];seen=set()
for m in pat.finditer(doc):
 k=(m.group('date'),int(m.group('number')))
 if k in seen:continue
 seen.add(k);editions.append({'date':k[0],'number':k[1]})
edition=max((e for e in editions if e['date']<=date.today().isoformat()),key=lambda e:(e['date'],e['number']))
params=urllib.parse.urlencode({'dtDiario':datetime.strptime(edition['date'],'%Y-%m-%d').strftime('%d/%m/%Y'),'cdCaderno':3,'tpDownload':'D'}); payload,_=get(DOWNLOAD+'?'+params,90)
with tempfile.NamedTemporaryFile(suffix='.pdf',delete=False) as f:f.write(payload);p=f.name
text=subprocess.run(['pdftotext','-layout',p,'-'],check=True,capture_output=True).stdout.decode('utf-8',errors='replace').replace('ﬁ','fi').replace('ﬂ','fl'); matches=list(cnj_re.finditer(text)); candidates=[]
for i,m in enumerate(matches):
 num=m.group(0); d=re.sub(r'\D','',num)
 if d[9:13]!=str(date.today().year):continue
 end=matches[i+1].start() if i+1<len(matches) else min(len(text),m.end()+1600); window=norm(text[m.start():end])
 if norm(TARGET) in window:candidates.append(num)
candidates=sorted(set(candidates),key=lambda n:int(re.sub(r'\D','',n)[:7]),reverse=True)[:100]
checked=[];confirmed=[]
for num in candidates:
 r=lookup(num);checked.append(r)
 if r.get('status')=='FOUND' and norm(r.get('classe'))==norm(TARGET) and r.get('distribuicao'):confirmed.append(r)
 time.sleep(.12)
Path('tmp/class81_output').mkdir(parents=True,exist_ok=True)
fields=['numero','numero_formatado','status','classe','distribuicao','distribuicao_raw','foro','vara','url','error']
with open('tmp/class81_output/class81_checked.tsv','w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields,delimiter='\t',extrasaction='ignore');w.writeheader();w.writerows(checked)
with open('tmp/class81_output/class81_confirmed.tsv','w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields,delimiter='\t',extrasaction='ignore');w.writeheader();w.writerows(confirmed)
Path('tmp/class81_output/summary.txt').write_text(f"edition={edition['date']}\ncandidates={len(candidates)}\nchecked={len(checked)}\nconfirmed={len(confirmed)}\n",encoding='utf-8')
print('edition',edition,'candidates',len(candidates),'checked',len(checked),'confirmed',len(confirmed))