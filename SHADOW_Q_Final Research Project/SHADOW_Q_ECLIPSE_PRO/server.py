"""SHADOW-Q ATLAS: local web server with genuine Qiskit-backed experiment API."""
import json, traceback, threading, webbrowser, argparse, time, sqlite3, hashlib, hmac, secrets, re, os, uuid
from http.cookies import SimpleCookie
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from dataclasses import asdict
from datetime import datetime, timezone
from shadowq.engine import Config, operations, groups_fixed, groups_adaptive, branch_circuits, exact_reconstruct, sampled_reconstruct, terms, full_expectation
from shadowq.adaptive import reconstruct as adaptive_reconstruct
from shadowq.shadows import estimate as shadow_estimate
ROOT=Path(__file__).resolve().parent
HISTORY=ROOT/'experiments';HISTORY.mkdir(exist_ok=True)
DB=ROOT/'research_accounts.sqlite3'
SESSIONS={}; LOCK=threading.RLock(); FAILURES={}
def connect_db():
    con=sqlite3.connect(DB,timeout=10)
    con.execute('CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, salt BLOB NOT NULL, password_hash BLOB NOT NULL, created_at TEXT NOT NULL)')
    return con
def hash_password(password,salt):
    return hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,310000)
def safe_user(row):return {'id':row[0],'name':row[1],'email':row[2]}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT/'web'),**kwargs)
    def session_user(self):
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception:return None
        token=cookie.get('sq_session')
        if not token:return None
        with LOCK:
            item=SESSIONS.get(token.value)
            if not item:return None
            user,expiry=item
            if time.time()>expiry:
                SESSIONS.pop(token.value,None);return None
            return user
    def json_response(self,code,obj,cookie=None):
        body=json.dumps(obj,allow_nan=False,default=str).encode()
        self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','same-origin');
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.path=='/api/auth/me':
            u=self.session_user();return self.json_response(200,{'authenticated':bool(u),'user':u})
        if self.path.startswith('/api/') and not self.session_user():return self.json_response(401,{'error':'Sign in required'})
        if self.path=='/api/history':
            records=[]
            for f in sorted(HISTORY.glob('*.json'),reverse=True)[:40]:
                try:
                    r=json.loads(f.read_text());
                    if r.get('researcher_id')!=self.session_user()['id']:continue
                    records.append({'id':f.stem,'time':r['time'],'mode':r['mode'],'config':r['config'],'energy_error':r.get('energy_error')})
                except (ValueError,KeyError):continue
            return self.json_response(200,{'records':records})
        if self.path.startswith('/api/history/'):
            name=self.path.rsplit('/',1)[-1]
            if not name.replace('_','').isalnum():return self.json_response(400,{'error':'Invalid ID'})
            p=HISTORY/(name+'.json')
            return self.json_response(200,json.loads(p.read_text())) if p.exists() and json.loads(p.read_text()).get('researcher_id')==self.session_user()['id'] else self.json_response(404,{'error':'Not found'})
        return super().do_GET()
    def do_POST(self):
        if self.path.startswith('/api/auth/'):
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<0 or length>5000:raise ValueError('Invalid request size')
                if self.path=='/api/auth/logout':
                    cookie=SimpleCookie()
                    try:cookie.load(self.headers.get('Cookie',''))
                    except Exception:pass
                    token=cookie.get('sq_session')
                    if token:
                        with LOCK:SESSIONS.pop(token.value,None)
                    return self.json_response(200,{'ok':True},'sq_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                if self.path not in ('/api/auth/register','/api/auth/login'):return self.json_response(404,{'error':'Unknown route'})
                data=json.loads(self.rfile.read(length));email=str(data.get('email','')).strip().lower();password=str(data.get('password',''))
                if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email) or len(email)>254:raise ValueError('Enter a valid email address')
                if self.path=='/api/auth/register':
                    name=str(data.get('name','')).strip()
                    if not 2<=len(name)<=50:raise ValueError('Researcher name must be 2–50 characters')
                    if not 10<=len(password)<=128:raise ValueError('Password must be 10–128 characters')
                    salt=secrets.token_bytes(16)
                    with connect_db() as con:
                        try:con.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',(uuid.uuid4().hex,name,email,salt,hash_password(password,salt),datetime.now(timezone.utc).isoformat()))
                        except sqlite3.IntegrityError:raise ValueError('Account already exists. Sign in instead.')
                else:
                    ip=self.client_address[0];key=(ip,email)
                    with LOCK:
                        attempts=[t for t in FAILURES.get(key,[]) if time.time()-t<300]
                        FAILURES[key]=attempts
                        if len(attempts)>=8:return self.json_response(429,{'error':'Too many attempts. Try again in five minutes.'})
                    with connect_db() as con:row=con.execute('SELECT id,name,email,salt,password_hash FROM users WHERE email=?',(email,)).fetchone()
                    if row is None or not hmac.compare_digest(hash_password(password,row[3]),row[4]):
                        with LOCK:FAILURES.setdefault(key,[]).append(time.time())
                        return self.json_response(401,{'error':'Incorrect email or password'})
                    with LOCK:FAILURES.pop(key,None)
                with connect_db() as con:row=con.execute('SELECT id,name,email FROM users WHERE email=?',(email,)).fetchone()
                user=safe_user(row);token=secrets.token_urlsafe(40)
                with LOCK:SESSIONS[token]=(user,time.time()+12*3600)
                return self.json_response(200,{'ok':True,'user':user},f'sq_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200')
            except (ValueError,TypeError,KeyError,json.JSONDecodeError) as e:return self.json_response(400,{'error':str(e)})
            except Exception as e:traceback.print_exc();return self.json_response(500,{'error':'Authentication service error'})
        if not self.session_user():return self.json_response(401,{'error':'Sign in required'})
        if self.path!='/api/run':return self.json_response(404,{'error':'Unknown route'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length>10000:raise ValueError('Request too large')
            data=json.loads(self.rfile.read(length)); n=int(data.get('n',7)); width=int(data.get('width',4)); layers=int(data.get('layers',2));shots=int(data.get('shots',256));seed=int(data.get('seed',7));angle=float(data.get('angle',0.4));mode=str(data.get('mode','exact'));policy=str(data.get('policy','adaptive'))
            if not(3<=n<=9 and 1<=width<=n and 1<=layers<=3 and 64<=shots<=4096 and 0.05<=angle<=1.5 and 0<=seed<=100000):raise ValueError('Invalid experiment parameters')
            if mode not in ('exact','finite','adaptive','uniform','shadows'):raise ValueError('Invalid mode')
            if policy not in ('adaptive','fixed'):raise ValueError('Invalid policy')
            cfg=Config(n=n,width=width,layers=layers,angle=angle,seed=seed,J=1.0,field=0.3)
            groups=groups_adaptive(cfg) if policy=='adaptive' else groups_fixed(cfg)
            branches,cuts=branch_circuits(cfg,groups)
            if len(branches)>64:raise ValueError('This configuration is too expensive for interactive execution. Reduce layers or increase fragment width.')
            selected=terms(cfg)
            start=time.perf_counter()
            if mode=='exact':r=exact_reconstruct(cfg,groups)
            elif mode=='finite':r=sampled_reconstruct(cfg,groups,selected_terms=selected[:min(6,len(selected))],shots_per_overlap=shots,max_experiments=300)
            elif mode in ('adaptive','uniform'):r=adaptive_reconstruct(cfg,groups,selected_terms=selected[:min(4,len(selected))],budget=max(4096,shots*32),pilot=16,adaptive=(mode=='adaptive'),max_circuits=300)
            else:
                raw_shadows=shadow_estimate(cfg,[t[0] for t in selected],shots=shots)
                mapped={name:{'estimate':raw_shadows[pauli]['estimate'],'reference':full_expectation(cfg,pauli),'weight':weight,'stderr':raw_shadows[pauli]['stderr']} for pauli,weight,name in selected}
                r={'method':'full-width Pauli classical shadows (uncut)','results':mapped,'energy_estimate':sum(v['estimate']*v['weight'] for v in mapped.values()),'energy_reference':sum(v['reference']*v['weight'] for v in mapped.values()),'total_shots':shots,'maximum_executed_qubits':n}
            elapsed=time.perf_counter()-start
            rows=[]
            for name,v in r.get('results',{}).items():
                ref=v.get('full',v.get('exact',v.get('reference')))
                val=v.get('reconstructed',v.get('sampled',v.get('estimate')))
                rows.append({'name':name,'reference':ref,'estimate':val,'weight':v.get('weight',1),'error':abs(ref-val) if ref is not None and val is not None else None})
            e_ref=r.get('energy_full',r.get('energy_reference'))
            e_est=r.get('energy_reconstructed',r.get('energy_sampled',r.get('energy_estimate')))
            result={'time':datetime.now(timezone.utc).isoformat(),'researcher_id':self.session_user()['id'],'mode':mode,'policy':policy,'config':asdict(cfg),'groups':groups,'cuts':cuts,'branches':len(branches),'elapsed_seconds':elapsed,'energy_reference':e_ref,'energy_estimate':e_est,'energy_error':abs(e_ref-e_est) if e_ref is not None and e_est is not None else None,'rows':rows,'raw':r,'maximum_executed_qubits':r.get('maximum_executed_qubits'),'warning':('Exact statevector calculations are a mathematical consistency check, not finite-shot evidence.' if mode=='exact' else 'Full-width classical shadows do not satisfy the fragment-width constraint.' if mode=='shadows' else 'Finite-shot results are stochastic. Hadamard-test circuits use one additional ancilla qubit.')}
            filename=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'.json';(HISTORY/filename).write_text(json.dumps(result,indent=2,default=str))
            return self.json_response(200,result)
        except (ValueError,TypeError,KeyError) as e:return self.json_response(400,{'error':str(e)})
        except Exception as e:
            traceback.print_exc();return self.json_response(500,{'error':str(e),'detail':'See terminal for traceback'})
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765);args=p.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'ECLIPSE PRO ready: http://localhost:{args.port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
