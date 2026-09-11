"""
2060 SOUND ARCHIVE - GPT Bridge Server v84 AUTOMATION STABILITY
"""
from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from pathlib import Path
from uuid import uuid4
from datetime import datetime
import base64, json, os, threading, re, urllib.request, urllib.parse, time, traceback
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
try:
    from openai import OpenAI
except Exception:
    OpenAI=None
SYSTEM_VERSION='v84-automation-stability-1'
RUNTIME_ID=uuid4().hex
DATA_DIR_ENV=os.getenv('AI_BRIDGE_DATA_DIR','').strip()
APP_DIR=Path(DATA_DIR_ENV or './ai_bridge_data').resolve(); APP_DIR.mkdir(parents=True,exist_ok=True)
JOBS_DIR=APP_DIR/'jobs'; JOBS_DIR.mkdir(exist_ok=True)
IMAGES_DIR=APP_DIR/'images'; IMAGES_DIR.mkdir(exist_ok=True)
VIDEO_JOBS_DIR=APP_DIR/'video_jobs'; VIDEO_JOBS_DIR.mkdir(exist_ok=True)
OPENART_JOBS_DIR=APP_DIR/'openart_jobs'; OPENART_JOBS_DIR.mkdir(exist_ok=True)
OPENART_INPUTS_DIR=APP_DIR/'openart_inputs'; OPENART_INPUTS_DIR.mkdir(exist_ok=True)
OPENART_RESULTS_DIR=APP_DIR/'openart_results'; OPENART_RESULTS_DIR.mkdir(exist_ok=True)
OPENART_WORKER_STATE_PATH=APP_DIR/'openart_worker_state.json'
BRIDGE_TOKEN=os.getenv('AI_BRIDGE_TOKEN','').strip()
OPENAI_API_KEY=os.getenv('OPENAI_API_KEY','').strip()
TEXT_MODEL=os.getenv('OPENAI_TEXT_MODEL','gpt-5.6-luna').strip()
IMAGE_MODEL=os.getenv('OPENAI_IMAGE_MODEL','gpt-image-2').strip()
ENABLE_IMAGE_GEN=os.getenv('ENABLE_IMAGE_GEN','true').lower()=='true'
ENABLE_SCENE_IMAGE_GEN=os.getenv('ENABLE_SCENE_IMAGE_GEN','true').lower()=='true'
PUBLIC_BASE_URL=os.getenv('PUBLIC_BASE_URL','').strip().rstrip('/')
def env_bool(*names,default=False):
    for name in names:
        raw=os.getenv(name)
        if raw is None:continue
        return str(raw).strip().lower() in ('1','true','yes','on','y')
    return bool(default)

# v78 default: Bridge handles text/image/QA; Colab renders from Google Drive.
DEFAULT_QUEUE_VIDEO=env_bool('DEFAULT_QUEUE_VIDEO','DEFAULT_QUEUE_VIDEO_JOB',default=False)
ENABLE_VIDEO_QUEUE=env_bool('ENABLE_VIDEO_QUEUE',default=False)
AUTO_RECOVER_INTERRUPTED_JOBS=env_bool('AUTO_RECOVER_INTERRUPTED_JOBS',default=False)
VIDEO_JOB_LEASE_SECONDS=max(120,int(os.getenv('VIDEO_JOB_LEASE_SECONDS','1800') or 1800))
JOB_RETENTION_DAYS=max(1,int(os.getenv('JOB_RETENTION_DAYS','30') or 30))
ENABLE_OPENART_QUEUE=env_bool('ENABLE_OPENART_QUEUE',default=True)
OPENART_JOB_RETENTION_DAYS=max(1,int(os.getenv('OPENART_JOB_RETENTION_DAYS','14') or 14))
OPENART_CLAIM_LEASE_SECONDS=max(120,int(os.getenv('OPENART_CLAIM_LEASE_SECONDS','1800') or 1800))
OPENART_WORKER_OFFLINE_SECONDS=max(30,int(os.getenv('OPENART_WORKER_OFFLINE_SECONDS','120') or 120))
OPENART_MAX_INPUT_BYTES=max(1,int(os.getenv('OPENART_MAX_INPUT_MB','12') or 12))*1024*1024
OPENART_MAX_RESULT_BYTES=max(10,int(os.getenv('OPENART_MAX_RESULT_MB','300') or 300))*1024*1024
MAX_CONCURRENT_JOBS=max(1,int(os.getenv('MAX_CONCURRENT_JOBS','1') or 1))
JOB_EXECUTOR=ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS,thread_name_prefix='archive-job')
STORAGE_PERSISTENT=(
    env_bool('AI_BRIDGE_PERSISTENT','AI_BRIDGE_PERSISTENT_STORAGE',default=False)
    or str(APP_DIR).startswith('/var/data')
    or str(APP_DIR).startswith('/mnt/data')
)
LAST_IMAGE_ERROR=''
LAST_JOB_ERROR=''
JSON_LOCK=threading.RLock()
client=OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None
app=FastAPI(title='2060 SOUND ARCHIVE GPT Bridge v84')
app.mount('/files',StaticFiles(directory=str(IMAGES_DIR)),name='files')
app.mount('/openart-files',StaticFiles(directory=str(OPENART_RESULTS_DIR)),name='openart-files')

class JobRequest(BaseModel):
    record:str
    title:str
    message:Optional[str]=''
    story:Optional[str]=''
    genre:Optional[str]=''
    mood:Optional[str]=''
    vocal:Optional[str]=''
    symbol:Optional[str]=''
    thumb_composition:Optional[str]=''
    source_title:Optional[str]=''
    source_url:Optional[str]=''
    source_genre:Optional[str]=''
    song_type:Optional[str]=''
    target_character:Optional[str]=''

    visual_concept:Optional[str]=''
    character_lock:Optional[str]=''
    background_style:Optional[str]=''
    negative_elements:Optional[str]=''
    base_image_rules:Optional[str]=''
    thumbnail_boost:Optional[str]=''
    scene_boost:Optional[str]=''
    intro_boost:Optional[str]=''
    verse_boost:Optional[str]=''
    pre_boost:Optional[str]=''
    chorus_boost:Optional[str]=''
    bridge_boost:Optional[str]=''
    final_boost:Optional[str]=''
    outro_boost:Optional[str]=''

    character_reference_url:Optional[str]=''
    character_reference_b64:Optional[str]=''
    character_reference_mime:Optional[str]=''
    character_reference_name:Optional[str]=''

    quality_check:bool=True
    quality_threshold:int=82
    max_regenerations:int=1

    # v82 task routing
    task_scope:Optional[str]='FULL'
    target_scenes:List[str]=Field(default_factory=list)
    existing_images:Dict[str,Dict[str,str]]=Field(default_factory=dict)
    # Existing source text is an image prompt, never a motion-prompt template.
    # Omitted prompt_source preserves the v83 generated-prompt behavior.
    prompt_source:str='GENERATED'
    thumbnail_prompt:str=''
    scene_image_prompts:Dict[str,str]=Field(default_factory=dict)
    generate_thumbnail:bool=True
    generate_scenes:bool=True
    generate_motion_prompts:bool=True
    generate_description:bool=False

    requested_by:Optional[str]=''
    job_type:Optional[str]='AI_IMAGE'
    queue_video_job:Optional[bool]=None
    force_new:bool=False

class VideoCompleteRequest(BaseModel):
    mv_video_url:str; short_hook_url:Optional[str]=''; short_chorus_url:Optional[str]=''; short_final_url:Optional[str]=''; note:Optional[str]=''
class VideoFailRequest(BaseModel):
    note:str

class OpenArtJobRequest(BaseModel):
    request_key:str
    # Stable for one user-approved submission, including retries after a timeout.
    submission_id:Optional[str]=''
    record:str
    title:Optional[str]=''
    scene:str
    prompt:str
    source_image_b64:str
    source_image_mime:Optional[str]='image/png'
    source_image_name:Optional[str]='source.png'
    model:Optional[str]='pixverseV6'
    duration:int=6
    resolution:Optional[str]='1080p'
    aspect_ratio:Optional[str]='16:9'
    output_filename:str
    estimated_credits:Optional[int]=0
    force_new:bool=False

class OpenArtProgressRequest(BaseModel):
    status:str='RUNNING'
    worker_id:Optional[str]=''
    claim_token:Optional[str]=''
    creation_id:Optional[str]=''
    note:Optional[str]=''

class OpenArtCompleteRequest(BaseModel):
    result_url:str
    worker_id:Optional[str]=''
    claim_token:Optional[str]=''
    creation_id:Optional[str]=''
    note:Optional[str]=''
    metadata:Dict[str,Any]=Field(default_factory=dict)

class OpenArtFailRequest(BaseModel):
    error:str
    worker_id:Optional[str]=''
    claim_token:Optional[str]=''
    creation_id:Optional[str]=''
    note:Optional[str]=''

class OpenArtWorkerHeartbeat(BaseModel):
    worker_id:str
    authenticated:bool=False
    cli_version:Optional[str]=''
    credits:Optional[float]=None
    status:Optional[str]='idle'
    current_job_id:Optional[str]=''
    note:Optional[str]=''


def check_auth(h):
    if not BRIDGE_TOKEN:return
    if (h or '').replace('Bearer ','').strip()!=BRIDGE_TOKEN:raise HTTPException(status_code=401,detail='Invalid token')

def should_queue_video(request_obj):
    requested=getattr(request_obj,'queue_video_job',None)
    if requested is None:requested=DEFAULT_QUEUE_VIDEO
    return bool(ENABLE_VIDEO_QUEUE and requested)

def job_path(j):return JOBS_DIR/f'{j}.json'
def queue_path(j):return VIDEO_JOBS_DIR/f'{j}.json'

def atomic_write_json(path,data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+f'.{uuid4().hex}.tmp')
    payload=json.dumps(data,ensure_ascii=False,indent=2)
    temp.write_text(payload,encoding='utf-8')
    os.replace(temp,path)

def read_json_file(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def save_job(d):
    d['updated_at']=datetime.now().isoformat(timespec='seconds')
    with JSON_LOCK:
        atomic_write_json(job_path(d['job_id']),d)

def load_job(j):
    p=job_path(j)
    if not p.exists():raise HTTPException(status_code=404,detail='Job not found')
    try:
        with JSON_LOCK:return read_json_file(p)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500,detail='Job data is corrupted')

def save_video_queue(d):
    with JSON_LOCK:atomic_write_json(queue_path(d['job_id']),d)

def delete_video_queue(job_id):
    try:queue_path(job_id).unlink(missing_ok=True)
    except Exception:pass


def openart_job_path(job_id):return OPENART_JOBS_DIR/f'{job_id}.json'

def save_openart_job(data):
    data['updated_at']=datetime.now().isoformat(timespec='seconds')
    with JSON_LOCK:atomic_write_json(openart_job_path(data['job_id']),data)

def load_openart_job(job_id):
    p=openart_job_path(job_id)
    if not p.exists():raise HTTPException(status_code=404,detail='OpenArt Job not found')
    try:
        with JSON_LOCK:return read_json_file(p)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500,detail='OpenArt Job data is corrupted')

def sanitize_openart_filename(value,fallback='output.mp4'):
    name=Path(str(value or fallback)).name
    name=re.sub(r'[^0-9A-Za-z가-힣._-]+','_',name).strip('._')
    if not name:name=fallback
    return name[:180]

def openart_input_path(job):
    return OPENART_INPUTS_DIR/str(job.get('input_filename') or '')

def openart_result_path(job):
    return OPENART_RESULTS_DIR/str(job.get('stored_filename') or '')

_OPENART_TX_STATE=threading.local()
OPENART_TERMINAL={'COMPLETED','FAILED','CANCELLED'}

@contextmanager
def openart_transaction():
    """Serialize lifecycle read/modify/write across threads and server processes.

    JSON files and this advisory lock must live on the same persistent volume.
    Reentrant calls use the outer transaction's file lock.
    """
    with JSON_LOCK:
        depth=getattr(_OPENART_TX_STATE,'depth',0)
        _OPENART_TX_STATE.depth=depth+1
        handle=None
        try:
            if not depth:
                handle=(APP_DIR/'openart_lifecycle.lock').open('a+b')
                if os.name=='nt':
                    import msvcrt
                    if handle.tell()==0:handle.write(b'0');handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(),msvcrt.LK_LOCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
            yield
        finally:
            _OPENART_TX_STATE.depth=depth
            if handle is not None:
                try:
                    if os.name=='nt':
                        import msvcrt
                        handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
                finally:handle.close()


def find_openart_job(request_key,submission_id=''):
    """Latest exact intent, or latest scene job for legacy reconciliation.

    Terminal and expired-result records remain idempotency evidence. A caller
    must explicitly request force_new with a new submission_id to regenerate.
    """
    matches=[]
    for p in OPENART_JOBS_DIR.glob('*.json'):
        try:j=read_json_file(p)
        except (OSError,ValueError):continue
        if str(j.get('request_key') or '')!=str(request_key or ''):continue
        if submission_id and str(submission_id) not in [str(j.get('submission_id') or '')]+list(j.get('submission_ids') or []):continue
        try:created=float(j.get('created_ts') or datetime.fromisoformat(j.get('created_at') or '').timestamp())
        except (ValueError,TypeError):created=p.stat().st_mtime
        matches.append((created,j))
    return max(matches,key=lambda item:item[0])[1] if matches else None


def find_active_openart_job(request_key):
    # Kept for compatibility with existing local tools. Terminal jobs are also
    # reused because an HTTP retry must never become a paid generation retry.
    return find_openart_job(request_key)


def openart_public_job(j):
    result_path=openart_result_path(j)
    available=bool(j.get('stored_filename') and result_path.is_file() and result_path.stat().st_size>=1024)
    return {
        'ok':True,'job_id':j['job_id'],'request_key':j.get('request_key',''),
        'submission_id':j.get('submission_id',''),'record':j.get('record',''),
        'scene':j.get('scene',''),'status':j.get('status',''),'creation_id':j.get('creation_id',''),
        'result_url':j.get('stored_result_url','') if available else '',
        'result_available':available,'estimated_credits':j.get('estimated_credits',0),
        'note':j.get('note',''),'error':j.get('error',''),'worker_id':j.get('worker_id',''),
        'created_at':j.get('created_at',''),'updated_at':j.get('updated_at',''),
        'completed_at':j.get('completed_at',''),'resume_stage':j.get('resume_stage',''),
        'requires_resume':bool(j.get('resume_stage')),'server_version':SYSTEM_VERSION,
    }


def openart_callback_guard(j,payload):
    """Ignore terminal repeats; reject stale owners before any mutation."""
    if str(j.get('status') or '').upper() in OPENART_TERMINAL:
        return {'ok':True,'status':j['status'],'ignored':True,'result_url':j.get('stored_result_url','')}
    owner=str(j.get('worker_id') or '')
    worker=str(payload.worker_id or '')
    token=str(getattr(payload,'claim_token','') or '')
    if not owner or (worker and worker!=owner):
        raise HTTPException(status_code=409,detail='OpenArt callback is not from the current worker owner')
    if token and token!=str(j.get('claim_token') or ''):
        raise HTTPException(status_code=409,detail='OpenArt claim token is stale')
    # Legacy workers can finish their first claim without a token. Recovered
    # claims require an explicit token, including when a worker reuses its ID.
    if int(j.get('claim_generation') or 0)>1 and not token:
        raise HTTPException(status_code=409,detail='Recovered OpenArt claim requires claim_token')
    if j.get('creation_id') and payload.creation_id and j['creation_id']!=payload.creation_id:
        raise HTTPException(status_code=409,detail='OpenArt creation_id does not match the existing provider job')
    return None


def parse_openart_worker_state():
    try:return read_json_file(OPENART_WORKER_STATE_PATH)
    except Exception:return {}

def save_openart_worker_state(data):
    state=dict(data or {})
    state['updated_at']=datetime.now().isoformat(timespec='seconds')
    state['updated_ts']=time.time()
    atomic_write_json(OPENART_WORKER_STATE_PATH,state)
    return state

def cleanup_old_openart_jobs():
    cutoff=time.time()-(OPENART_JOB_RETENTION_DAYS*86400)
    removed=0
    with openart_transaction():
        for p in list(OPENART_JOBS_DIR.glob('*.json')):
            try:
                if p.stat().st_mtime>=cutoff:continue
                j=read_json_file(p)
                if str(j.get('status') or '').upper() not in OPENART_TERMINAL:continue
                if j.get('media_expired'):continue
                for field,path_fn in (('input_filename',openart_input_path),('stored_filename',openart_result_path)):
                    if j.get(field):path_fn(j).unlink(missing_ok=True)
                # Preserve the small JSON record so a delayed HTTP retry cannot
                # recreate an already charged job after media retention expires.
                j['media_expired']=True
                j['note']='보관 기간 만료로 Bridge 미디어 정리됨. 기존 생성 이력 유지; 새 생성은 명시적으로 요청해야 합니다.'
                save_openart_job(j);removed+=1
            except (OSError,ValueError):continue
    return removed


def requeue_expired_openart_jobs():
    now=time.time();recovered=0
    with openart_transaction():
        for p in list(OPENART_JOBS_DIR.glob('*.json')):
            try:
                j=read_json_file(p)
                if str(j.get('status') or '').upper() not in {'CLAIMED','SUBMITTING','RUNNING','DOWNLOADING'}:continue
                lease=float(j.get('lease_expires_ts') or 0)
                if not lease or lease>now:continue
                if j.get('source_result_url'):
                    j['status']='QUEUED';j['resume_stage']='DOWNLOAD_RESULT'
                elif j.get('creation_id'):
                    j['status']='QUEUED';j['resume_stage']='POLL_CREATION'
                else:
                    # A worker can crash after provider acceptance but before
                    # reporting the ID. Blind resubmission could charge twice.
                    j['status']='NEEDS_RECONCILIATION';j['resume_stage']='VERIFY_PROVIDER_SUBMISSION'
                j['previous_worker_id']=j.get('worker_id','')
                j['note']='Worker 응답 만료: 기존 생성 확인/이어서 처리 필요. 자동 신규 생성 중지.'
                j.pop('worker_id',None);j.pop('lease_expires_ts',None)
                j.pop('download_token',None)
                save_openart_job(j);recovered+=1
            except (OSError,ValueError):continue
    return recovered


def is_mp4_header(data):
    head=bytes(data[:64] if data else b'')
    return b'ftyp' in head or b'moov' in head or b'mdat' in head

def download_openart_result(url,target):
    temp=Path(str(target)+'.part')
    total=0
    req=urllib.request.Request(str(url),headers={'User-Agent':'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req,timeout=180) as resp,temp.open('wb') as out:
            while True:
                chunk=resp.read(1024*1024)
                if not chunk:break
                total+=len(chunk)
                if total>OPENART_MAX_RESULT_BYTES:raise ValueError('OpenArt result exceeds size limit')
                out.write(chunk)
        if total<1024:raise ValueError('OpenArt result is empty')
        with temp.open('rb') as f:head=f.read(64)
        if not is_mp4_header(head):raise ValueError('OpenArt result is not a valid MP4/MOV file')
        os.replace(temp,target)
        return total
    finally:
        if temp.exists():
            try:temp.unlink()
            except Exception:pass

def cleanup_old_jobs():
    cutoff=time.time()-(JOB_RETENTION_DAYS*86400)
    active={'PENDING','PROCESSING','WAITING_VIDEO','VIDEO_RENDERING'}
    removed=0
    for p in list(JOBS_DIR.glob('*.json')):
        try:
            if p.stat().st_mtime>=cutoff:continue
            j=read_json_file(p)
            if str(j.get('status') or '').upper() in active:continue
            jid=str(j.get('job_id') or p.stem)
            p.unlink(missing_ok=True)
            delete_video_queue(jid)
            removed+=1
        except Exception:
            continue
    return removed

def find_active_job(record):
    target=str(record or '').strip()
    if not target:return None
    active={'PENDING','PROCESSING'}
    with JSON_LOCK:
        files=sorted(JOBS_DIR.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
        for p in files[:500]:
            try:
                j=read_json_file(p)
                req=j.get('request') or {}
                if str(req.get('record') or '').strip()!=target:continue
                if str(j.get('status') or '').upper() not in active:continue
                # 서버 재시작 전 in-memory 작업은 현재 프로세스에서 실행 중이 아님.
                # 비용 보호를 위해 자동 유료 재생성을 기본적으로 수행하지 않는다.
                if str(j.get('runtime_id') or '')!=RUNTIME_ID:
                    j['status']='INTERRUPTED'
                    j.setdefault('result',{})['note']='Bridge 재시작으로 진행 중 작업이 중단되었습니다. 비용 보호를 위해 자동 재생성하지 않았습니다.'
                    j['interrupted_at']=datetime.now().isoformat(timespec='seconds')
                    atomic_write_json(p,j)
                    continue
                return j
            except Exception:
                continue
    return None

def parse_iso(value):
    try:return datetime.fromisoformat(str(value or '').replace('Z','+00:00'))
    except Exception:return None

def requeue_expired_video_jobs():
    now=datetime.now()
    recovered=0
    for p in list(VIDEO_JOBS_DIR.glob('*.json')):
        try:
            q=read_json_file(p)
            if str(q.get('status') or '')!='VIDEO_RENDERING':continue
            lease=parse_iso(q.get('lease_started_at'))
            if not lease or (now-lease.replace(tzinfo=None)).total_seconds()<VIDEO_JOB_LEASE_SECONDS:continue
            j=load_job(q['job_id'])
            j['status']='WAITING_VIDEO'
            j.setdefault('result',{})['note']='영상 Worker lease 만료로 자동 재대기 처리했습니다.'
            save_job(j)
            q['status']='WAITING_VIDEO'
            q.pop('lease_started_at',None)
            q.pop('lease_expires_at',None)
            save_video_queue(q)
            recovered+=1
        except HTTPException:
            try:p.unlink(missing_ok=True)
            except Exception:pass
        except Exception as e:
            print(f'[LEASE RECOVERY ERROR] {p.name}: {type(e).__name__}: {e}',flush=True)
    return recovered

def recover_interrupted_jobs_on_startup():
    """
    Render restart leaves old PENDING/PROCESSING JSON files without a running thread.
    Default: mark INTERRUPTED only. Automatic rerun is opt-in because it can spend image credits again.
    """
    interrupted=[]
    recovered=[]
    for p in sorted(JOBS_DIR.glob('*.json')):
        try:
            j=read_json_file(p)
            status=str(j.get('status') or '').upper()
            old_runtime=str(j.get('runtime_id') or '')
            if status not in ('PENDING','PROCESSING'):continue
            if old_runtime==RUNTIME_ID:continue

            jid=str(j.get('job_id') or p.stem)
            if AUTO_RECOVER_INTERRUPTED_JOBS:
                j['status']='PENDING'
                j['runtime_id']=RUNTIME_ID
                j.setdefault('result',{})['note']='Bridge 시작 시 중단 Job 자동복구 대기'
                save_job(j)
                recovered.append(jid)
            else:
                j['status']='INTERRUPTED'
                j['interrupted_at']=datetime.now().isoformat(timespec='seconds')
                j.setdefault('result',{})['note']='Bridge 재시작으로 작업이 중단되었습니다. 자동 재생성은 비용 보호를 위해 OFF입니다.'
                save_job(j)
                interrupted.append(jid)
        except Exception as e:
            print(f'[STARTUP RECOVERY ERROR] {p.name}: {type(e).__name__}: {e}',flush=True)

    for jid in recovered:
        JOB_EXECUTOR.submit(process_job,jid)

    if interrupted:
        print(f'[STARTUP] {len(interrupted)} jobs marked INTERRUPTED (cost protection)',flush=True)
    if recovered:
        print(f'[STARTUP] {len(recovered)} jobs auto-recovered',flush=True)
    return {'interrupted':len(interrupted),'recovered':len(recovered)}


def context(d):
    p=[f'Song title: {d.title}.',f'Record: {d.record}.']
    if d.source_title:p.append(f'CrackAI source work: {d.source_title}.')
    if d.song_type:p.append(f'Song type: {d.song_type}.')
    if d.target_character:p.append(f'Focus character: {d.target_character}.')
    if d.genre or d.source_genre:p.append('Genre: '+', '.join(x for x in [d.genre,d.source_genre] if x)+'.')
    if d.mood:p.append(f'Mood: {d.mood}.')
    if d.message:p.append(f'Core message: {d.message}.')
    if d.story:p.append(f'Story/world: {d.story}.')
    if d.symbol:p.append(f'Visual motifs: {d.symbol}.')
    return ' '.join(p)

def prompt_tuning(d):
    p=[]
    if d.visual_concept:p.append(f'Overall visual concept: {d.visual_concept}.')
    if d.character_lock:p.append(f'Locked protagonist appearance: {d.character_lock}.')
    if d.background_style:p.append(f'Background / lighting / atmosphere guidance: {d.background_style}.')
    if d.negative_elements:p.append(f'Avoid these elements: {d.negative_elements}.')
    if d.base_image_rules:p.append(f'Base image rules: {d.base_image_rules}.')
    return ' '.join(p)

def scene_boost_for(d,scene):
    key={'INTRO':'intro_boost','VERSE':'verse_boost','PRE':'pre_boost','CHORUS':'chorus_boost','BRIDGE':'bridge_boost','FINAL':'final_boost','OUTRO':'outro_boost'}.get(scene,'')
    value=getattr(d,key,'') if key else ''
    parts=[]
    if d.scene_boost:parts.append(f'Common scene tuning: {d.scene_boost}.')
    if value:parts.append(f'{scene} scene tuning: {value}.')
    return ' '.join(parts)

def thumb_prompt(d):
    parts=[
        'Create a professional YouTube music thumbnail prompt in English.',
        '16:9 landscape, premium cinematic anime illustration, adult character only.',
        'One strong focal subject, clean composition, dramatic lighting, high contrast.',
        'Leave readable negative space for Korean title text; do not put text inside the generated image.',
        'No logo, no watermark. Every clearly visible human hand must have exactly five digits total (four fingers and one thumb), with anatomically plausible joints. No extra, missing, fused, duplicated, forked, or branching fingers; no duplicated arms or hands.',
        context(d), prompt_tuning(d),
        f'Preferred composition: {d.thumb_composition}.' if d.thumb_composition else '',
        f'Thumbnail-specific tuning: {d.thumbnail_boost}.' if d.thumbnail_boost else ''
    ]
    return ' '.join(x for x in parts if x).strip()

def desc_prompt(d):return 'Write a concise Korean YouTube music description. Use 3-5 short paragraphs, emotional and music-first. Do not invent facts. If CrackAI source exists, mention this is an OST-like/concept song based on it. '+context(d)

def common_motion(d):
    if uses_existing_image_prompts(d):
        return ' '.join([
            'Animate the supplied source image. Preserve its exact subjects, face, hairstyle, outfit, accessories, body proportions, setting, lighting, and color palette.',
            'Use smooth cinematic camera motion, subtle breathing and blinking when appropriate, gentle hair and cloth movement, and parallax depth.',
            'Do not redesign the source image, introduce new characters or objects, or change anatomy.'
        ])
    return ' '.join([
        'The same adult character from the reference image. Preserve the exact face, hairstyle, outfit, accessories, body proportions, and color palette.',
        'Create cinematic 3D-like motion with realistic movement, subtle breathing, blinking, hair physics, cloth physics, parallax depth, and smooth camera motion.',
        'Premium anime-to-3D look, stable anatomy, no redesign, no extra limbs, no face distortion.',
        context(d), prompt_tuning(d),
        f'Common scene tuning: {d.scene_boost}.' if d.scene_boost else ''
    ])

def scenes(d):
    c=common_motion(d)
    s={
        'INTRO':'Opening establishing shot. Calm motion and gentle mood-setting camera movement.',
        'VERSE':'Narrative verse shot. Natural body movement, moderate emotional pace, story development.',
        'PRE':'Pre-chorus build-up. Increase anticipation, wind, particles, light intensity, and rising camera energy.',
        'CHORUS':'Climactic chorus shot. Stronger wind, brighter light, energetic dolly/orbit motion, vivid depth.',
        'BRIDGE':'Bridge contrast shot. More intimate or reflective camera language before the final climax.',
        'FINAL':'Final chorus climax. Highest emotional energy, luminous character, dynamic hair and cloth, hero composition.',
        'OUTRO':'Outro resolution. Slower softer motion, easing camera, emotional afterglow.'
    }
    if uses_existing_image_prompts(d):
        return {k:' '.join([c,v,'Source image description: '+d.scene_image_prompts[k]]).strip()
                for k,v in s.items() if k in d.scene_image_prompts}
    return {k:' '.join([c,v,scene_boost_for(d,k)]).strip() for k,v in s.items()}
def call_text(p):
    if not client:return p
    try:
        r=client.responses.create(model=TEXT_MODEL,input=p); return getattr(r,'output_text',None) or p
    except Exception:return p

def safe_ext_from_mime(mime,name=''):
    m=(mime or '').lower()
    n=(name or '').lower()
    if 'jpeg' in m or n.endswith('.jpg') or n.endswith('.jpeg'):return '.jpg'
    if 'webp' in m or n.endswith('.webp'):return '.webp'
    return '.png'

SCENE_KEYS=['INTRO','VERSE','PRE','CHORUS','BRIDGE','FINAL','OUTRO']
ALL_IMAGE_KEYS=['THUMBNAIL']+SCENE_KEYS

SCOPE_LABELS={
    'FULL':'전체 이미지',
    'THUMBNAIL_ONLY':'썸네일만',
    'SCENES_ALL':'장면 이미지 7장',
    'SELECTED_SCENES':'선택 장면',
    'QA_ONLY':'QA만 다시 검사',
    'REGENERATE_FAILED':'QA 실패 장면 재생성',
    'PROMPTS_ONLY':'프롬프트만 갱신'
}

def normalize_scene_key(value):
    s=re.sub(r'[^A-Z0-9]','',str(value or '').upper())
    aliases={
        'THUMB':'THUMBNAIL','THUMBNAIL':'THUMBNAIL',
        'INTRO':'INTRO','VERSE':'VERSE',
        'PRE':'PRE','PRECHORUS':'PRE',
        'CHORUS':'CHORUS','BRIDGE':'BRIDGE',
        'FINAL':'FINAL','FINALCHORUS':'FINAL',
        'OUTRO':'OUTRO'
    }
    return aliases.get(s,'')

def normalize_scope(value):
    s=re.sub(r'[^A-Z0-9]','_',str(value or 'FULL').upper()).strip('_')
    aliases={
        'FULL':'FULL','ALL':'FULL','ALL_IMAGES':'FULL',
        'THUMBNAIL':'THUMBNAIL_ONLY','THUMBNAIL_ONLY':'THUMBNAIL_ONLY',
        'SCENES':'SCENES_ALL','SCENES_ALL':'SCENES_ALL','ALL_SCENES':'SCENES_ALL',
        'SELECTED':'SELECTED_SCENES','SELECTED_SCENES':'SELECTED_SCENES',
        'QA':'QA_ONLY','QA_ONLY':'QA_ONLY',
        'REGENERATE_FAILED':'REGENERATE_FAILED','FAILED_ONLY':'REGENERATE_FAILED',
        'PROMPTS':'PROMPTS_ONLY','PROMPTS_ONLY':'PROMPTS_ONLY'
    }
    return aliases.get(s,'FULL')

def resolve_targets(d):
    scope=normalize_scope(d.task_scope)
    supplied=[]
    for value in (d.target_scenes or []):
        key=normalize_scene_key(value)
        if key and key not in supplied:supplied.append(key)

    if scope=='FULL':return ['THUMBNAIL']+SCENE_KEYS
    if scope=='THUMBNAIL_ONLY':return ['THUMBNAIL']
    if scope=='SCENES_ALL':return list(SCENE_KEYS)
    if scope in ('SELECTED_SCENES','REGENERATE_FAILED'):
        return supplied
    if scope=='QA_ONLY':
        return supplied or [normalize_scene_key(k) for k in (d.existing_images or {}).keys() if normalize_scene_key(k)]
    return []

def uses_existing_image_prompts(d):
    return str(d.prompt_source or 'GENERATED').strip().upper()=='EXISTING'

def validate_image_prompt_source(d):
    """Validate before queueing or paid work; preserve prompt text byte-for-byte."""
    mode=str(d.prompt_source or 'GENERATED').strip().upper()
    if mode not in ('GENERATED','EXISTING'):
        raise ValueError('prompt_source must be GENERATED or EXISTING')
    d.prompt_source=mode
    if mode!='EXISTING':return {}
    prompts={}
    for raw_key,value in d.scene_image_prompts.items():
        key=normalize_scene_key(raw_key)
        if key not in SCENE_KEYS:
            raise ValueError('Unknown scene_image_prompts key: '+str(raw_key))
        if key in prompts and prompts[key]!=value:
            raise ValueError('Conflicting image prompts for '+key)
        prompts[key]=value
    d.scene_image_prompts=prompts
    for raw_key in d.target_scenes:
        if not normalize_scene_key(raw_key):
            raise ValueError('Unknown target_scenes key: '+str(raw_key))
    targets=resolve_targets(d)
    scope=normalize_scope(d.task_scope)
    if scope in ('SELECTED_SCENES','REGENERATE_FAILED','QA_ONLY') and not targets:
        raise ValueError('target_scenes is required for '+scope)
    supplied={'THUMBNAIL':d.thumbnail_prompt,**prompts}
    missing=[key for key in targets if not supplied.get(key,'').strip()]
    if missing:
        raise ValueError('Existing image prompt required for: '+', '.join(missing))
    return {key:supplied[key] for key in targets}

def prepare_existing_images(d):
    paths={};urls={};errors={}
    for raw_key,payload in (d.existing_images or {}).items():
        key=normalize_scene_key(raw_key)
        if not key or not isinstance(payload,dict):continue
        try:
            raw=None
            mime=str(payload.get('mime') or 'image/png')
            name=str(payload.get('name') or f'{key}.png')
            if payload.get('b64'):
                raw=base64.b64decode(payload.get('b64'))
            elif payload.get('url'):
                req=urllib.request.Request(str(payload.get('url')),headers={'User-Agent':'Mozilla/5.0'})
                with urllib.request.urlopen(req,timeout=45) as resp:
                    raw=resp.read()
                    mime=resp.headers.get_content_type() or mime
            if not raw:raise ValueError('empty image payload')
            ext=safe_ext_from_mime(mime,name)
            path=IMAGES_DIR/f'{d.record}_existing_{key}{ext}'
            path.write_bytes(raw)
            paths[key]=path
            urls[key]=f'{PUBLIC_BASE_URL}/files/{path.name}' if PUBLIC_BASE_URL else ''
        except Exception as e:
            errors[key]=f'{type(e).__name__}: {e}'
    return paths,urls,errors

def prepare_character_reference(d):
    if not PUBLIC_BASE_URL:
        return None,''
    try:
        raw=None
        mime=d.character_reference_mime or 'image/png'
        name=d.character_reference_name or 'character_reference.png'
        if d.character_reference_b64:
            raw=base64.b64decode(d.character_reference_b64)
        elif d.character_reference_url:
            req=urllib.request.Request(d.character_reference_url,headers={'User-Agent':'Mozilla/5.0'})
            with urllib.request.urlopen(req,timeout=45) as resp:
                raw=resp.read()
                mime=resp.headers.get_content_type() or mime
                name=Path(urllib.parse.urlparse(d.character_reference_url).path).name or name
        if not raw:
            return None,''
        ext=safe_ext_from_mime(mime,name)
        path=IMAGES_DIR/f'{d.record}_character_reference{ext}'
        path.write_bytes(raw)
        url=f'{PUBLIC_BASE_URL}/files/{path.name}'
        return path,url
    except Exception as e:
        print(f'[REFERENCE ERROR] {d.record}: {type(e).__name__}: {e}',flush=True)
        return None,''

def gen_image(p,record,suffix='thumbnail',reference_path=None,preserve_prompt=False):
    global LAST_IMAGE_ERROR

    if not ENABLE_IMAGE_GEN:
        LAST_IMAGE_ERROR='ENABLE_IMAGE_GEN=false'
        return '',LAST_IMAGE_ERROR
    if not client:
        LAST_IMAGE_ERROR='OpenAI client unavailable: OPENAI_API_KEY missing or openai package unavailable'
        return '',LAST_IMAGE_ERROR
    if not PUBLIC_BASE_URL:
        LAST_IMAGE_ERROR='PUBLIC_BASE_URL is not configured'
        return '',LAST_IMAGE_ERROR

    try:
        r=None
        if reference_path and Path(reference_path).exists():
            ref_instruction=(
                'Use the provided image as the identity reference for the protagonist. '
                'Preserve the same adult character identity, face, hairstyle, eye color, outfit identity, accessories, and overall palette, '
                'while creating the new requested composition and scene. Do not copy the original background unless requested. '
            )
            try:
                with open(reference_path,'rb') as ref_file:
                    r=client.images.edit(model=IMAGE_MODEL,image=ref_file,prompt=p if preserve_prompt else ref_instruction+p,size='1536x1024')
            except Exception as edit_error:
                print(f'[REFERENCE EDIT FALLBACK] {record} {suffix}: {type(edit_error).__name__}: {edit_error}',flush=True)
                r=None
        if r is None:
            r=client.images.generate(model=IMAGE_MODEL,prompt=p,size='1536x1024')

        if not getattr(r,'data',None):
            LAST_IMAGE_ERROR='Image API returned no data'
            return '',LAST_IMAGE_ERROR
        item=r.data[0]
        b64=getattr(item,'b64_json',None)
        remote_url=getattr(item,'url',None)
        fn=f'{record}_{suffix}_{uuid4().hex[:10]}.png'
        target=IMAGES_DIR/fn
        if b64:
            target.write_bytes(base64.b64decode(b64))
        elif remote_url:
            urllib.request.urlretrieve(remote_url,target)
        else:
            LAST_IMAGE_ERROR='Image API response contained neither b64_json nor url'
            return '',LAST_IMAGE_ERROR
        public_url=f'{PUBLIC_BASE_URL}/files/{fn}'
        LAST_IMAGE_ERROR=''
        print(f'[IMAGE OK] {record} {suffix} -> {public_url}',flush=True)
        return public_url,''
    except Exception as e:
        LAST_IMAGE_ERROR=f'{type(e).__name__}: {e}'
        print(f'[IMAGE ERROR] {record} {suffix}: {LAST_IMAGE_ERROR}',flush=True)
        return '',LAST_IMAGE_ERROR

def extract_json_object(text):
    s=(text or '').strip()
    try:return json.loads(s)
    except Exception:pass
    m=re.search(r'\{.*\}',s,re.S)
    if not m:return None
    try:return json.loads(m.group(0))
    except Exception:return None

def _qa_call_json(content, purpose='QA'):
    try:
        r=client.responses.create(model=TEXT_MODEL,input=[{'role':'user','content':content}])
        raw=getattr(r,'output_text',None) or ''
        obj=extract_json_object(raw) or {}
        return obj,''
    except Exception as e:
        err=f'{type(e).__name__}: {e}'
        print(f'[{purpose} ERROR] {err}',flush=True)
        return {},err

def anatomy_check_image(image_url,d,label,reference_url=''):
    """Dedicated hard gate for visible hands/fingers/arms before general aesthetic QA."""
    if not client:
        return {'pass':False,'hands_visible':0,'findings':['Anatomy QA unavailable'],'qa_error':'OpenAI client unavailable'}
    if not image_url:
        return {'pass':False,'hands_visible':0,'findings':['Generated image missing'],'qa_error':'Generated image missing'}

    text=(
        'You are a STRICT anatomy inspector for a production image. This is a hard safety gate, not an aesthetic review. '
        f'Inspect the generated image labeled {label} very carefully, zooming attention conceptually to EVERY visible hand, finger, thumb, wrist, arm and limb. '
        'Count digits on each clearly visible human hand independently. A normal clearly visible hand must have exactly five digits total: four fingers plus one thumb. '
        'FAIL anatomy_pass if ANY clearly visible hand has six or more digits, four or fewer digits when they should be visible, duplicated fingers, fused fingers, forked/branching fingers, impossible thumb placement, malformed palm/wrist, duplicated hands, duplicated arms, or extra limbs. '
        'Do not overlook small background hands. Do not excuse an obvious six-finger hand because the overall image looks good. '
        'If a hand is genuinely hidden by crop, clothing, another object, perspective, or a closed fist, do not invent a digit count; mark it occluded instead. '
        'Also fail if the face/eyes or major limb structure is clearly anatomically corrupted. '
        'Return ONLY JSON with keys: anatomy_pass (boolean), hands_visible (integer), hand_findings (array of short strings), other_anatomy_findings (array of short strings), regeneration_instruction (short English correction prompt).'
    )
    content=[{'type':'input_text','text':text},{'type':'input_image','image_url':image_url}]
    if reference_url:
        content.append({'type':'input_text','text':'Character identity reference follows. Use it only for identity context; anatomy must be judged from the generated image.'})
        content.append({'type':'input_image','image_url':reference_url})
    obj,err=_qa_call_json(content,'ANATOMY QA')
    if err:
        return {'pass':False,'hands_visible':0,'findings':['Anatomy QA service error'],'regeneration_instruction':'','qa_error':err}

    hands_visible=max(0,int(obj.get('hands_visible',0) or 0))
    hf=obj.get('hand_findings',[]) if isinstance(obj.get('hand_findings',[]),list) else [str(obj.get('hand_findings',''))]
    of=obj.get('other_anatomy_findings',[]) if isinstance(obj.get('other_anatomy_findings',[]),list) else [str(obj.get('other_anatomy_findings',''))]
    findings=[str(x).strip() for x in (hf+of) if str(x).strip()][:12]
    passed=bool(obj.get('anatomy_pass',False))
    instruction=str(obj.get('regeneration_instruction','')).strip()
    return {'pass':passed,'hands_visible':hands_visible,'findings':findings,'regeneration_instruction':instruction[:1200],'qa_error':''}

def quality_check_image(image_url,d,label,expected_prompt='',reference_url=''):
    if not d.quality_check:
        return {'score':None,'pass':True,'issues':[],'regeneration_instruction':'','qa_error':'','anatomy_pass':True,'hands_visible':0,'anatomy_findings':[]}
    if not client:
        return {'score':None,'pass':False,'issues':['QA unavailable: OpenAI client unavailable'],'regeneration_instruction':'','qa_error':'OpenAI client unavailable','anatomy_pass':False,'hands_visible':0,'anatomy_findings':['QA unavailable']}
    if not image_url:
        return {'score':0.0,'pass':False,'issues':['Generated image missing'],'regeneration_instruction':'Regenerate the missing image successfully before continuing.','qa_error':'Generated image missing','anatomy_pass':False,'hands_visible':0,'anatomy_findings':['Generated image missing']}

    # Pass 1: dedicated anatomy/hands hard gate.
    anatomy=anatomy_check_image(image_url,d,label,reference_url)
    if anatomy.get('qa_error'):
        return {'score':None,'pass':False,'issues':['Anatomy QA service error'],'regeneration_instruction':'','qa_error':anatomy.get('qa_error',''),'anatomy_pass':False,'hands_visible':anatomy.get('hands_visible',0),'anatomy_findings':anatomy.get('findings',[])}

    threshold=max(50,min(100,int(d.quality_threshold or 82)))
    qa_text=(
        'You are an image QA reviewer for an anime music-video production pipeline. '
        f'Review the generated image labeled {label}. Score it from 0 to 100. Pass threshold is {threshold}. '
        'Check: natural anatomy; face/eyes; composition and cinematic depth; prompt adherence; adult appearance; clean detailed rendering; no unintended text/logo/watermark. '
        'Hands and fingers are already checked by a separate strict anatomy gate, but mention any additional anatomy problem you notice. '
        'If a character reference image is supplied, also check identity consistency: face, hairstyle, eye color, outfit identity, accessories and palette. '
        'Return ONLY JSON with keys score (number), pass (boolean), issues (array of short strings), regeneration_instruction (short English correction prompt). '
        f'Expected scene instructions: {expected_prompt[:2500]}'
    )
    content=[{'type':'input_text','text':qa_text},{'type':'input_image','image_url':image_url}]
    if reference_url:
        content.append({'type':'input_text','text':'The next image is the character identity reference.'})
        content.append({'type':'input_image','image_url':reference_url})

    obj,err=_qa_call_json(content,'GENERAL QA')
    if err:
        return {'score':None,'pass':False,'issues':['QA service error'],'regeneration_instruction':'','qa_error':err,'anatomy_pass':bool(anatomy.get('pass')),'hands_visible':anatomy.get('hands_visible',0),'anatomy_findings':anatomy.get('findings',[])}

    score=float(obj.get('score',0))
    general_pass=bool(obj.get('pass',score>=threshold)) and score>=threshold
    issues=obj.get('issues',[]) if isinstance(obj.get('issues',[]),list) else [str(obj.get('issues',''))]
    issues=[str(x).strip() for x in issues if str(x).strip()]
    anatomy_pass=bool(anatomy.get('pass',False))
    anatomy_findings=anatomy.get('findings',[]) or []

    # HARD RULE: anatomy failure overrides any high overall score.
    passed=bool(general_pass and anatomy_pass)
    if not anatomy_pass:
        issues=['HARD ANATOMY FAIL'] + list(anatomy_findings) + issues
        score=min(score,59.0)

    general_instruction=str(obj.get('regeneration_instruction','')).strip()
    anatomy_instruction=str(anatomy.get('regeneration_instruction','')).strip()
    instruction=' '.join(x for x in [anatomy_instruction,general_instruction] if x).strip()
    if not anatomy_pass:
        hard_fix='Every clearly visible human hand must have exactly five digits total: four fingers and one thumb. Correct any extra, missing, fused, duplicated, forked, or branching fingers and any malformed or duplicated hands/arms.'
        instruction=(hard_fix+' '+instruction).strip()

    return {
        'score':round(score,1),'pass':passed,'issues':issues[:12],
        'regeneration_instruction':instruction[:1600],'qa_error':'',
        'anatomy_pass':anatomy_pass,'hands_visible':anatomy.get('hands_visible',0),
        'anatomy_findings':anatomy_findings[:12]
    }

def scene_image_prompt(d,scene,motion_prompt):
    if uses_existing_image_prompts(d):
        # No generic scene text, boosts, or motion instructions alter source art.
        return d.scene_image_prompts[scene]
    scene_notes={
        'INTRO':'Opening establishing scene, wide shot, calm world introduction and atmospheric depth.',
        'VERSE':'Narrative medium shot, natural pose, emotional storytelling, moderate energy.',
        'PRE':'Pre-chorus build-up, anticipation, stronger light and wind, dynamic three-quarter composition.',
        'CHORUS':'Emotional chorus climax, powerful hero shot, vivid lighting, energetic particles and depth.',
        'BRIDGE':'Reflective bridge scene, intimate camera, emotional contrast, slightly darker atmosphere.',
        'FINAL':'Final chorus climax, strongest heroic composition, luminous character, emotional release.',
        'OUTRO':'Quiet ending shot, slower emotional atmosphere, lingering afterglow, cinematic closure.'
    }
    parts=[
        'Create a 16:9 cinematic anime music-video keyframe.','Adult character only.',
        'Keep one consistent protagonist design across all scenes: same face, hairstyle, eye color, outfit identity, accessories, body proportions, and color palette.',
        'Premium detailed anime illustration with realistic cinematic lighting and strong depth.',
        'No text, no logo, no watermark. Every clearly visible human hand must have exactly five digits total (four fingers and one thumb), with natural anatomy. No extra, missing, fused, duplicated, forked, or branching fingers; no duplicated arms, hands, or limbs.',
        context(d),prompt_tuning(d),scene_notes.get(scene,''),scene_boost_for(d,scene),'Motion intent: '+motion_prompt
    ]
    return ' '.join(x for x in parts if x).strip()

def generate_with_qa(d,prompt,suffix,label,reference_path=None,reference_url=''):
    max_retry=max(0,min(2,int(d.max_regenerations or 0)))
    attempts=0
    final_url=''; final_error=''; final_qa={'score':None,'pass':True,'issues':[],'regeneration_instruction':'','qa_error':''}
    current_prompt=prompt
    for attempt in range(max_retry+1):
        attempts=attempt
        unique_suffix=suffix if attempt==0 else f'{suffix}_retry{attempt}'
        if uses_existing_image_prompts(d):
            url,error=gen_image(current_prompt,d.record,unique_suffix,reference_path,preserve_prompt=True)
        else:
            url,error=gen_image(current_prompt,d.record,unique_suffix,reference_path)
        final_url,final_error=url,error
        if not url or error:
            break
        final_qa=quality_check_image(url,d,label,current_prompt,reference_url)
        if final_qa.get('pass',True):
            break
        if attempt<max_retry:
            if not uses_existing_image_prompts(d):
                correction=final_qa.get('regeneration_instruction') or '; '.join(final_qa.get('issues') or [])
                current_prompt=(prompt+' Regenerate this image and correct the following QA issues: '+correction+
                                 ' Preserve character identity and intended composition. Every clearly visible human hand must have exactly five digits total: four fingers and one thumb. No extra, missing, fused, duplicated, forked, or branching fingers. Keep wrists, arms, hands and limbs anatomically natural. No text or watermark.')
            print(f'[QA RETRY] {d.record} {label}: score={final_qa.get("score")} attempt={attempt+1}',flush=True)
    return final_url,final_error,final_qa,attempts

def gen_scene_images(d,sp,reference_path=None,reference_url=''):
    if not ENABLE_SCENE_IMAGE_GEN:
        return {},{'CONFIG':'ENABLE_SCENE_IMAGE_GEN=false'},{},0
    urls={}; errors={}; quality={}; regen_total=0
    consistency_ref=reference_url
    for key in ['INTRO','VERSE','PRE','CHORUS','BRIDGE','FINAL','OUTRO']:
        p=scene_image_prompt(d,key,sp.get(key,''))
        url,error,qa,retries=generate_with_qa(d,p,f'scene_{key}',key,reference_path,consistency_ref)
        regen_total+=retries
        if url:
            urls[key]=url
            if not consistency_ref and key=='INTRO':consistency_ref=url
        if error:errors[key]=error
        quality[key]=qa
    return urls,errors,quality,regen_total

def generate_selected_images(d,scope,targets,tp,sp,reference_path=None,reference_url=''):
    thumb='';thumb_error='';thumb_quality={};thumb_retries=0
    scene_urls={};scene_errors={};scene_quality={};scene_regens=0

    if 'THUMBNAIL' in targets:
        thumb,thumb_error,thumb_quality,thumb_retries=generate_with_qa(
            d,tp,'thumbnail','THUMBNAIL',reference_path,reference_url
        )

    consistency_ref=reference_url
    for key in [x for x in SCENE_KEYS if x in targets]:
        if not ENABLE_SCENE_IMAGE_GEN:
            scene_errors[key]='ENABLE_SCENE_IMAGE_GEN=false'
            scene_quality[key]={'score':None,'pass':False,'issues':['scene generation disabled'],'regeneration_instruction':'','qa_error':'scene generation disabled'}
            continue
        prompt=scene_image_prompt(d,key,sp.get(key,''))
        url,error,qa,retries=generate_with_qa(
            d,prompt,f'scene_{key}',key,reference_path,consistency_ref
        )
        scene_regens+=retries
        if url:
            scene_urls[key]=url
            if not consistency_ref and key=='INTRO':consistency_ref=url
        if error:scene_errors[key]=error
        scene_quality[key]=qa

    return {
        'thumbnail_url':thumb,'thumbnail_error':thumb_error,
        'thumbnail_quality':thumb_quality,'thumbnail_retries':thumb_retries,
        'scene_urls':scene_urls,'scene_errors':scene_errors,
        'scene_quality':scene_quality,'scene_regens':scene_regens
    }

def qa_existing_images(d,targets,existing_urls,tp,sp,reference_url=''):
    quality={};errors={}
    for key in targets:
        url=existing_urls.get(key,'')
        if not url:
            errors[key]='existing image missing'
            quality[key]={'score':0.0,'pass':False,'issues':['Existing image missing'],'regeneration_instruction':'Provide the existing image before QA.','qa_error':'Existing image missing'}
            continue
        expected=tp if key=='THUMBNAIL' else scene_image_prompt(d,key,sp.get(key,''))
        quality[key]=quality_check_image(url,d,key,expected,reference_url)
    return quality,errors

def build_quality_summary(d,quality_report):
    numeric=[float(v['score']) for v in quality_report.values() if isinstance(v,dict) and v.get('score') is not None]
    average=round(sum(numeric)/len(numeric),1) if numeric else None
    failed=[k for k,v in quality_report.items() if isinstance(v,dict) and not v.get('pass',True)]
    qa_errors=[k for k,v in quality_report.items() if isinstance(v,dict) and v.get('qa_error')]
    if not d.quality_check:status='미사용'
    elif failed:status='검토 필요'
    elif qa_errors:status='검수 오류'
    else:status='통과'
    return status,average,failed,qa_errors

def make_progress_text(targets,thumb_url,scene_urls,scope):
    if scope=='PROMPTS_ONLY':return '프롬프트 완료'
    thumb_target=1 if 'THUMBNAIL' in targets else 0
    thumb_done=1 if thumb_url else 0
    scene_targets=[x for x in targets if x in SCENE_KEYS]
    scene_done=sum(1 for x in scene_targets if scene_urls.get(x))
    parts=[]
    if thumb_target:parts.append(f'썸네일 {thumb_done}/{thumb_target}')
    if scene_targets:parts.append(f'장면 {scene_done}/{len(scene_targets)}')
    if scope=='QA_ONLY':parts.append(f'QA {len(targets)}장')
    return ' · '.join(parts) or '처리 완료'

def _process_job_impl(job_id):
    job=load_job(job_id)
    d=JobRequest(**job['request'])
    d.quality_threshold=max(50,min(100,int(d.quality_threshold or 82)))
    d.max_regenerations=max(0,min(2,int(d.max_regenerations or 0)))
    scope=normalize_scope(d.task_scope)
    targets=resolve_targets(d)
    used_image_prompts=validate_image_prompt_source(d)

    if scope in ('SELECTED_SCENES','REGENERATE_FAILED') and not targets:
        raise ValueError('target_scenes is required for selected-scene generation')

    job['status']='PROCESSING'
    job['task_scope']=scope
    job['target_scenes']=targets
    save_job(job)

    reference_path,reference_public_url=prepare_character_reference(d)
    existing_paths,existing_urls,existing_errors=prepare_existing_images(d)

    # Do not keep large base64 payloads in persistent job JSON.
    if isinstance(job.get('request'),dict):
        job['request']['character_reference_b64']=''
        if job['request'].get('existing_images'):
            compact={}
            for k,v in (job['request'].get('existing_images') or {}).items():
                if isinstance(v,dict):compact[k]={'name':v.get('name',''),'mime':v.get('mime','')}
            job['request']['existing_images']=compact
        save_job(job)

    # v82 removes duplicated YouTube-description generation. Gemini owns copywriting.
    tp=d.thumbnail_prompt if uses_existing_image_prompts(d) else thumb_prompt(d)
    cm=common_motion(d)
    sp=scenes(d)

    thumb='';thumb_error='';scene_urls={};scene_errors={};quality_report={};regen_total=0

    if scope=='PROMPTS_ONLY':
        pass
    elif scope=='QA_ONLY':
        quality_report,qa_input_errors=qa_existing_images(
            d,targets,existing_urls,tp,sp,reference_public_url
        )
        scene_errors.update(existing_errors)
        scene_errors.update(qa_input_errors)
    else:
        generated=generate_selected_images(
            d,scope,targets,tp,sp,reference_path,reference_public_url
        )
        thumb=generated['thumbnail_url']
        thumb_error=generated['thumbnail_error']
        scene_urls=generated['scene_urls']
        scene_errors=generated['scene_errors']
        regen_total=generated['thumbnail_retries']+generated['scene_regens']
        if 'THUMBNAIL' in targets:
            quality_report['THUMBNAIL']=generated['thumbnail_quality']
        quality_report.update(generated['scene_quality'])

    quality_status,quality_average,failed_quality,qa_errors=build_quality_summary(d,quality_report)

    err_parts=[]
    if thumb_error:err_parts.append('THUMB: '+thumb_error)
    for k,v in list(scene_errors.items())[:5]:err_parts.append(f'{k}: {v}')
    if len(scene_errors)>5:err_parts.append(f'+{len(scene_errors)-5} more')
    err_summary=' | '.join(err_parts)

    target_scene_keys=[x for x in targets if x in SCENE_KEYS]
    completed_targets=[]
    if 'THUMBNAIL' in targets and thumb:completed_targets.append('THUMBNAIL')
    completed_targets += [x for x in target_scene_keys if scene_urls.get(x)]

    progress=make_progress_text(targets,thumb,scene_urls,scope)
    result={
        'task_scope':scope,
        'prompt_source':d.prompt_source,
        'used_image_prompts':used_image_prompts,
        'task_scope_label':SCOPE_LABELS.get(scope,scope),
        'target_scenes':targets,
        'completed_targets':completed_targets,
        'image_progress':progress,
        'thumbnail_prompt':tp if scope!='QA_ONLY' else '',
        'thumbnail_image_url':thumb,
        'generated_description':'',
        'common_motion_prompt':cm if d.generate_motion_prompts else '',
        'scene_prompts':sp if d.generate_motion_prompts else {},
        'scene_image_urls':scene_urls,
        'scene_image_errors':scene_errors,
        'scene_images_generated':len(scene_urls),
        'image_errors_summary':err_summary[:1500],
        'character_reference_url':reference_public_url,
        'image_quality_status':quality_status,
        'image_quality_average':quality_average,
        'image_regenerations':regen_total,
        'image_quality_report':quality_report,
        'quality_failed_scenes':failed_quality,
        'mv_prompt_status':'완료' if d.generate_motion_prompts else '',
        'mv_video_url':'','short_hook_url':'','short_chorus_url':'','short_final_url':'',
        'note':''
    }
    job['result']=result

    expected_count=len(targets)
    generated_or_qa_count=(len(completed_targets) if scope!='QA_ONLY' else len(quality_report))
    generation_block=bool(scope not in ('QA_ONLY','PROMPTS_ONLY') and (generated_or_qa_count!=expected_count or thumb_error or scene_errors))
    quality_block=bool(d.quality_check and scope!='PROMPTS_ONLY' and (failed_quality or qa_errors))

    if generation_block or quality_block:
        delete_video_queue(job_id)

    if generation_block:
        job['status']='IMAGE_ERROR'
        missing=[x for x in targets if x not in completed_targets]
        result['quality_failed_scenes']=list(dict.fromkeys((failed_quality or [])+missing))
        result['image_quality_status']='검수 불가'
        result['note']=f'{SCOPE_LABELS.get(scope,scope)} 실패 / {progress}'
        if missing:result['note']+=' / 누락: '+', '.join(missing)
        if err_summary:result['note']+=' / 오류: '+err_summary[:900]
    elif quality_block:
        job['status']='QUALITY_REVIEW'
        reason=list(dict.fromkeys((failed_quality or [])+(qa_errors or [])))
        result['quality_failed_scenes']=reason
        result['note']=f'{SCOPE_LABELS.get(scope,scope)} QA 검토 필요: '+', '.join(reason)
        if quality_average is not None:result['note']+=f' / 평균 {quality_average:.1f}점'
        if regen_total:result['note']+=f' / 자동 재생성 {regen_total}회'
    elif scope=='FULL' and should_queue_video(d):
        q={
            'job_id':job_id,'record':d.record,'title':d.title,
            'common_motion_prompt':cm,'scene_prompts':sp,
            'scene_image_urls':scene_urls,'scene_image_errors':scene_errors,
            'scene_images_generated':len(scene_urls),
            'image_quality_status':quality_status,
            'image_quality_average':quality_average,
            'image_regenerations':regen_total,
            'created_at':datetime.now().isoformat(timespec='seconds'),
            'status':'WAITING_VIDEO'
        }
        save_video_queue(q)
        job['status']='WAITING_VIDEO'
        result['note']=f'전체 이미지 완료 / {progress} / QA {quality_status} / Colab Worker 대기'
    else:
        job['status']='DONE'
        result['note']=f'{SCOPE_LABELS.get(scope,scope)} 완료 / {progress}'
        if scope!='PROMPTS_ONLY':
            result['note']+=f' / QA {quality_status}'
            if quality_average is not None:result['note']+=f' {quality_average:.1f}점'
            if regen_total:result['note']+=f' / 자동 재생성 {regen_total}회'
        if scope=='FULL':result['note']+=' / Direct Drive 렌더 준비'
    save_job(job)

def process_job(job_id):
    global LAST_JOB_ERROR
    try:
        _process_job_impl(job_id)
        LAST_JOB_ERROR=''
    except Exception as e:
        LAST_JOB_ERROR=f'{type(e).__name__}: {e}'
        print(f'[JOB ERROR] {job_id}: {LAST_JOB_ERROR}',flush=True)
        traceback.print_exc()
        try:
            j=load_job(job_id)
            j['status']='FAILED'
            result=j.setdefault('result',{})
            result['note']='Bridge 작업 실패: '+LAST_JOB_ERROR[:1200]
            save_job(j)
            delete_video_queue(job_id)
        except Exception:
            pass

@app.post('/jobs')
def create_job(payload:JobRequest,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    try:
        validate_image_prompt_source(payload)
    except ValueError as error:
        raise HTTPException(status_code=422,detail=str(error)) from error
    cleanup_old_jobs()
    with JSON_LOCK:
        if not payload.force_new:
            existing=find_active_job(payload.record)
            if existing:
                existing_scope=normalize_scope((existing.get('request') or {}).get('task_scope','FULL'))
                requested_scope=normalize_scope(payload.task_scope)
                existing_request=existing.get('request') or {}
                existing_mode=str(existing_request.get('prompt_source') or 'GENERATED').upper()
                same_prompt_request=(existing_mode==payload.prompt_source)
                if same_prompt_request and uses_existing_image_prompts(payload):
                    # A different saved prompt/selection must never reuse stale work.
                    old_request=JobRequest(**existing_request)
                    same_prompt_request=(
                        resolve_targets(old_request)==resolve_targets(payload)
                        and validate_image_prompt_source(old_request)==validate_image_prompt_source(payload)
                    )
                if existing_scope==requested_scope and same_prompt_request:
                    return {
                        'job_id':existing['job_id'],
                        'status':'전송완료',
                        'reused':True,
                        'note':'같은 곡·작업 범위의 진행 중 Job을 재사용했습니다.'
                    }
        jid=uuid4().hex
        request_data=payload.model_dump() if hasattr(payload,'model_dump') else payload.dict()
        request_data['queue_video_job']=should_queue_video(payload)
        job={
            'job_id':jid,'status':'PENDING',
            'created_at':datetime.now().isoformat(timespec='seconds'),
            'request':request_data,
            'system_version':SYSTEM_VERSION,
            'runtime_id':RUNTIME_ID
        }
        save_job(job)
    JOB_EXECUTOR.submit(process_job,jid)
    return {'job_id':jid,'status':'전송완료','reused':False,'note':'GPT Bridge 작업 접수 완료'}

@app.get('/jobs/{job_id}')
def get_job(job_id:str,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    j=load_job(job_id)
    r=j.get('result',{})
    return {
        'job_id':j['job_id'],'status':j['status'],'server_version':SYSTEM_VERSION,
        'runtime_id':str(j.get('runtime_id') or '')[:8],
        'task_scope':r.get('task_scope',(j.get('request') or {}).get('task_scope','FULL')),
        'prompt_source':r.get('prompt_source',(j.get('request') or {}).get('prompt_source','GENERATED')),
        'used_image_prompts':r.get('used_image_prompts',{}),
        'task_scope_label':r.get('task_scope_label',''),
        'target_scenes':r.get('target_scenes',[]),
        'completed_targets':r.get('completed_targets',[]),
        'image_progress':r.get('image_progress',''),
        'queue_video_job':bool((j.get('request') or {}).get('queue_video_job',False)),
        'thumbnail_prompt':r.get('thumbnail_prompt',''),'thumbnail_image_url':r.get('thumbnail_image_url',''),
        'generated_description':r.get('generated_description',''),'common_motion_prompt':r.get('common_motion_prompt',''),
        'scene_prompts':r.get('scene_prompts',{}),'scene_image_urls':r.get('scene_image_urls',{}),
        'scene_image_errors':r.get('scene_image_errors',{}),'scene_images_generated':r.get('scene_images_generated',0),
        'image_errors_summary':r.get('image_errors_summary',''),'character_reference_url':r.get('character_reference_url',''),
        'image_quality_status':r.get('image_quality_status',''),'image_quality_average':r.get('image_quality_average',''),
        'image_regenerations':r.get('image_regenerations',0),'image_quality_report':r.get('image_quality_report',{}),
        'quality_failed_scenes':r.get('quality_failed_scenes',[]),'mv_prompt_status':r.get('mv_prompt_status',''),
        'mv_video_url':r.get('mv_video_url',''),'short_hook_url':r.get('short_hook_url',''),
        'short_chorus_url':r.get('short_chorus_url',''),'short_final_url':r.get('short_final_url',''),
        'note':r.get('note','')
    }
@app.get('/video-jobs/next')
def next_video_job(authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    recovered=requeue_expired_video_jobs()
    with JSON_LOCK:
        for p in sorted(VIDEO_JOBS_DIR.glob('*.json'),key=lambda x:x.stat().st_mtime):
            try:
                d=read_json_file(p)
                j=load_job(d['job_id'])
                if j.get('status')!='WAITING_VIDEO':
                    continue
                r=j.get('result',{}) or {}
                generated=int(r.get('scene_images_generated',d.get('scene_images_generated',0)) or 0)
                image_errors=r.get('scene_image_errors',d.get('scene_image_errors',{})) or {}
                qa_status=str(r.get('image_quality_status',d.get('image_quality_status','')) or '')
                if generated!=7 or image_errors or qa_status in ('검토 필요','검수 오류','검수 불가'):
                    j['status']='IMAGE_ERROR' if (generated!=7 or image_errors) else 'QUALITY_REVIEW'
                    j.setdefault('result',{})['note']=f'영상 큐 안전검사에서 보류: 장면 이미지 {generated}/7 / QA {qa_status or "미확인"}'
                    save_job(j);delete_video_queue(j['job_id'])
                    continue
                lease_started=datetime.now()
                j['status']='VIDEO_RENDERING'
                j.setdefault('result',{})['note']='Colab Worker가 영상 작업을 가져갔습니다.'
                save_job(j)
                d['status']='VIDEO_RENDERING'
                d['lease_started_at']=lease_started.isoformat(timespec='seconds')
                d['lease_expires_at']=datetime.fromtimestamp(lease_started.timestamp()+VIDEO_JOB_LEASE_SECONDS).isoformat(timespec='seconds')
                save_video_queue(d)
                d['lease_recovered_jobs']=recovered
                return d
            except HTTPException:
                try:p.unlink(missing_ok=True)
                except Exception:pass
            except Exception as e:
                print(f'[VIDEO QUEUE ERROR] {p.name}: {type(e).__name__}: {e}',flush=True)
                continue
    return {'job_id':'','status':'EMPTY','lease_recovered_jobs':recovered}

@app.post('/video-jobs/{job_id}/complete')
def complete_video_job(job_id:str,payload:VideoCompleteRequest,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    j=load_job(job_id);r=j.setdefault('result',{})
    r['mv_video_url']=payload.mv_video_url
    r['short_hook_url']=payload.short_hook_url or ''
    r['short_chorus_url']=payload.short_chorus_url or ''
    r['short_final_url']=payload.short_final_url or ''
    r['note']=payload.note or '영상 렌더 완료'
    j['status']='DONE';save_job(j);delete_video_queue(job_id)
    return {'ok':True,'status':'DONE'}

@app.post('/video-jobs/{job_id}/fail')
def fail_video_job(job_id:str,payload:VideoFailRequest,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    j=load_job(job_id);j['status']='FAILED';j.setdefault('result',{})['note']=payload.note
    save_job(j);delete_video_queue(job_id)
    return {'ok':True,'status':'FAILED'}

@app.post('/video-jobs/{job_id}/requeue')
def requeue_video_job(job_id:str,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    j=load_job(job_id)
    r=j.get('result',{}) or {}
    if int(r.get('scene_images_generated',0) or 0)!=7:
        raise HTTPException(status_code=409,detail='Scene images are incomplete')
    q={
        'job_id':job_id,'record':j.get('request',{}).get('record',''),
        'title':j.get('request',{}).get('title',''),
        'common_motion_prompt':r.get('common_motion_prompt',''),
        'scene_prompts':r.get('scene_prompts',{}),
        'scene_image_urls':r.get('scene_image_urls',{}),
        'scene_images_generated':r.get('scene_images_generated',0),
        'image_quality_status':r.get('image_quality_status',''),
        'created_at':datetime.now().isoformat(timespec='seconds'),'status':'WAITING_VIDEO'
    }
    j['status']='WAITING_VIDEO';j.setdefault('result',{})['note']='영상 Job 수동 재대기'
    save_job(j);save_video_queue(q)
    return {'ok':True,'status':'WAITING_VIDEO'}


@app.post('/openart-jobs')
def create_openart_job(payload:OpenArtJobRequest,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    if not ENABLE_OPENART_QUEUE:raise HTTPException(status_code=503,detail='OpenArt queue is disabled')
    if not payload.request_key.strip():raise HTTPException(status_code=400,detail='request_key is required')
    with openart_transaction():
        cleanup_old_openart_jobs();requeue_expired_openart_jobs()
        submission_id=str(payload.submission_id or '').strip()
        existing=find_openart_job(payload.request_key,submission_id) if submission_id else None
        if existing is None and not payload.force_new:
            existing=find_openart_job(payload.request_key)
        if existing:
            if submission_id and submission_id!=existing.get('submission_id'):
                aliases=existing.setdefault('submission_ids',[])
                if submission_id not in aliases:aliases.append(submission_id);save_openart_job(existing)
            reply=openart_public_job(existing);reply['reused']=True
            reply['note']='Existing OpenArt submission reused; no new generation was queued. '+str(existing.get('note') or '')
            return reply
        try:image_bytes=base64.b64decode(payload.source_image_b64,validate=True)
        except Exception:raise HTTPException(status_code=400,detail='Invalid source_image_b64')
        if not image_bytes or len(image_bytes)>OPENART_MAX_INPUT_BYTES:
            raise HTTPException(status_code=413,detail=f'OpenArt source image must be 1..{OPENART_MAX_INPUT_BYTES} bytes')
        job_id='oa_'+uuid4().hex
        ext=Path(payload.source_image_name or '').suffix.lower()
        if ext not in ('.png','.jpg','.jpeg','.webp'):ext='.png'
        input_filename=f'{job_id}{ext}'
        (OPENART_INPUTS_DIR/input_filename).write_bytes(image_bytes)
        job={
            'job_id':job_id,'request_key':payload.request_key,'submission_id':submission_id,
            'record':payload.record,'title':payload.title,
            'scene':payload.scene,'prompt':payload.prompt,'model':payload.model or 'pixverseV6',
            'duration':max(1,min(15,int(payload.duration or 6))),'resolution':payload.resolution or '1080p',
            'aspect_ratio':payload.aspect_ratio or '16:9','output_filename':sanitize_openart_filename(payload.output_filename),
            'estimated_credits':int(payload.estimated_credits or 0),'input_filename':input_filename,
            'input_mime':payload.source_image_mime or 'image/png','input_bytes':len(image_bytes),
            'status':'QUEUED','note':'OpenArt Worker 대기','created_at':datetime.now().isoformat(timespec='seconds'),
            'created_ts':time.time(),'claim_generation':0,
        }
        save_openart_job(job)
        return openart_public_job(job)


# Register lookup before the dynamic job ID route.
@app.get('/openart-jobs/lookup')
def lookup_openart_job(request_key:str,submission_id:str='',authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    if not request_key.strip():raise HTTPException(status_code=400,detail='request_key is required')
    with openart_transaction():
        job=find_openart_job(request_key,submission_id)
        return dict(openart_public_job(job),found=True) if job else {'ok':True,'found':False,'job_id':''}


@app.get('/openart-jobs/{job_id}')
def get_openart_job(job_id:str,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    with openart_transaction():return openart_public_job(load_openart_job(job_id))


@app.get('/openart-jobs/{job_id}/input')
def get_openart_job_input(job_id:str,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization);j=load_openart_job(job_id);p=openart_input_path(j)
    if not p.exists():raise HTTPException(status_code=404,detail='OpenArt input not found')
    return FileResponse(str(p),media_type=j.get('input_mime') or 'application/octet-stream',filename=p.name)


@app.get('/openart-jobs/next/claim')
def claim_openart_job(worker_id:str='worker',authorization:Optional[str]=Header(default=None),supports_resume:bool=False):
    check_auth(authorization)
    if not ENABLE_OPENART_QUEUE:return {'job_id':'','status':'DISABLED'}
    if not str(worker_id).strip():raise HTTPException(status_code=400,detail='worker_id is required')
    with openart_transaction():
        recovered=requeue_expired_openart_jobs();resume_waiting=0
        for p in sorted(OPENART_JOBS_DIR.glob('*.json'),key=lambda x:x.stat().st_mtime):
            try:
                j=read_json_file(p)
                if str(j.get('status') or '').upper()!='QUEUED':continue
                resume_stage=j.get('resume_stage') or ''
                if resume_stage and not supports_resume:
                    resume_waiting+=1;continue
                j['status']='CLAIMED';j['worker_id']=worker_id
                j['claim_generation']=int(j.get('claim_generation') or 0)+1
                # Existing jobs created before v84 have no counter; a resumed
                # claim still requires a token even when this is its first one.
                if resume_stage:j['claim_generation']=max(2,j['claim_generation'])
                j['claim_token']=uuid4().hex
                j['lease_expires_ts']=time.time()+OPENART_CLAIM_LEASE_SECONDS
                j['note']='기존 OpenArt 생성 이어서 처리' if resume_stage else 'OpenArt Worker가 작업을 가져갔습니다.'
                save_openart_job(j)
                return {
                    'job_id':j['job_id'],'request_key':j.get('request_key',''),'submission_id':j.get('submission_id',''),
                    'record':j.get('record',''),'title':j.get('title',''),'scene':j.get('scene',''),
                    'prompt':j.get('prompt',''),'model':j.get('model','pixverseV6'),'duration':j.get('duration',6),
                    'resolution':j.get('resolution','1080p'),'aspect_ratio':j.get('aspect_ratio','16:9'),
                    'output_filename':j.get('output_filename','output.mp4'),
                    'input_url':f"{PUBLIC_BASE_URL}/openart-jobs/{j['job_id']}/input" if PUBLIC_BASE_URL else f"/openart-jobs/{j['job_id']}/input",
                    'estimated_credits':j.get('estimated_credits',0),'recovered':recovered,'status':'CLAIMED',
                    'creation_id':j.get('creation_id',''),'source_result_url':j.get('source_result_url',''),
                    'resume_stage':resume_stage or 'SUBMIT','allow_new_generation':not bool(resume_stage),
                    'claim_token':j['claim_token'],'claim_generation':j['claim_generation'],
                }
            except (OSError,ValueError) as e:
                print(f'[OPENART CLAIM ERROR] {p.name}: {type(e).__name__}: {e}',flush=True)
    return {'job_id':'','status':'EMPTY','recovered':recovered,'resume_waiting':resume_waiting}


@app.post('/openart-jobs/{job_id}/progress')
def update_openart_job(job_id:str,payload:OpenArtProgressRequest,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    with openart_transaction():
        j=load_openart_job(job_id);ignored=openart_callback_guard(j,payload)
        if ignored:return ignored
        status=str(payload.status or 'RUNNING').upper()
        if status not in {'CLAIMED','SUBMITTING','RUNNING','DOWNLOADING'}:
            raise HTTPException(status_code=400,detail='Use complete/fail endpoints for terminal status')
        if j.get('download_token'):
            return {'ok':True,'status':j['status'],'ignored':True}
        j['status']=status
        if payload.creation_id:j['creation_id']=payload.creation_id
        if payload.note:j['note']=payload.note
        j['lease_expires_ts']=time.time()+OPENART_CLAIM_LEASE_SECONDS
        save_openart_job(j);return {'ok':True,'status':j['status']}


@app.post('/openart-jobs/{job_id}/complete')
def complete_openart_job(job_id:str,payload:OpenArtCompleteRequest,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    with openart_transaction():
        j=load_openart_job(job_id);ignored=openart_callback_guard(j,payload)
        if ignored:return ignored
        if j.get('download_token'):
            return {'ok':True,'status':'DOWNLOADING','ignored':True}
        operation=uuid4().hex
        j['status']='DOWNLOADING';j['download_token']=operation
        j['creation_id']=payload.creation_id or j.get('creation_id','')
        j['source_result_url']=payload.result_url;j['resume_stage']='DOWNLOAD_RESULT'
        j['note']='OpenArt 결과를 Bridge에 저장 중'
        j['lease_expires_ts']=time.time()+OPENART_CLAIM_LEASE_SECONDS
        save_openart_job(j)
        filename=f"{job_id}_{sanitize_openart_filename(j.get('output_filename') or 'output.mp4')}"
    # Do network I/O outside the lifecycle lock. A unique staging name and
    # operation check prevent an old downloader overwriting a recovered result.
    target=OPENART_RESULTS_DIR/filename
    staging=OPENART_RESULTS_DIR/f'{filename}.{operation}.download'
    try:
        size=download_openart_result(payload.result_url,staging)
    except Exception as e:
        staging.unlink(missing_ok=True)
        with openart_transaction():
            current=load_openart_job(job_id)
            if current.get('download_token')!=operation:
                return {'ok':True,'status':current['status'],'ignored':True}
            current.pop('download_token',None)
            # Keep the provider ID/result URL and let the current worker retry
            # download. Lease recovery may only resume, never generate anew.
            current['status']='RUNNING'
            current['error']=f'Result download failed: {type(e).__name__}: {e}'
            current['note']=current['error'];save_openart_job(current)
        raise HTTPException(status_code=502,detail=current['error'])
    with openart_transaction():
        current=load_openart_job(job_id)
        if current.get('download_token')!=operation or current.get('status')!='DOWNLOADING':
            staging.unlink(missing_ok=True)
            return {'ok':True,'status':current['status'],'ignored':True}
        os.replace(staging,target)
        public=f'{PUBLIC_BASE_URL}/openart-files/{urllib.parse.quote(filename)}' if PUBLIC_BASE_URL else f'/openart-files/{urllib.parse.quote(filename)}'
        current['status']='COMPLETED';current['stored_filename']=filename
        current['stored_result_url']=public;current['result_bytes']=size;current['metadata']=payload.metadata or {}
        current['note']=payload.note or 'OpenArt 생성 및 Bridge 저장 완료'
        current['completed_at']=datetime.now().isoformat(timespec='seconds')
        current.pop('lease_expires_ts',None);current.pop('download_token',None)
        current.pop('resume_stage',None);current.pop('error',None)
        save_openart_job(current)
        return {'ok':True,'status':'COMPLETED','result_url':public,'result_bytes':size}


@app.post('/openart-jobs/{job_id}/fail')
def fail_openart_job(job_id:str,payload:OpenArtFailRequest,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    with openart_transaction():
        j=load_openart_job(job_id);ignored=openart_callback_guard(j,payload)
        if ignored:return ignored
        if j.get('download_token'):
            return {'ok':True,'status':j['status'],'ignored':True}
        j['status']='FAILED';j['creation_id']=payload.creation_id or j.get('creation_id','')
        j['error']=payload.error;j['note']=payload.note or payload.error
        j.pop('lease_expires_ts',None);save_openart_job(j)
        return {'ok':True,'status':'FAILED'}


@app.post('/openart-jobs/{job_id}/cancel')
def cancel_openart_job(job_id:str,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    with openart_transaction():
        j=load_openart_job(job_id);status=str(j.get('status') or '').upper()
        if status in OPENART_TERMINAL:
            return {'ok':True,'status':status,'note':j.get('note','')}
        # Cancellation is only guaranteed while the job has never been claimed.
        if status!='QUEUED' or j.get('creation_id') or j.get('resume_stage') or j.get('claim_generation'):
            raise HTTPException(status_code=409,detail='OpenArt job may already be submitted; cancellation cannot be guaranteed. Reconcile the existing generation first.')
        j['status']='CANCELLED';j['note']='OpenArt 제출 전 사용자 취소'
        j.pop('lease_expires_ts',None);save_openart_job(j)
        return {'ok':True,'status':'CANCELLED','note':j['note']}


@app.post('/openart-worker/heartbeat')
def openart_worker_heartbeat(payload:OpenArtWorkerHeartbeat,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    state=save_openart_worker_state(payload.model_dump() if hasattr(payload,'model_dump') else payload.dict())
    return {'ok':True,'updated_at':state['updated_at']}

@app.get('/openart-worker/status')
def openart_worker_status(authorization:Optional[str]=Header(default=None)):
    check_auth(authorization);state=parse_openart_worker_state();updated=float(state.get('updated_ts') or 0)
    state['online']=bool(updated and time.time()-updated<=OPENART_WORKER_OFFLINE_SECONDS)
    return state

@app.on_event('startup')
def startup_event():
    cleanup_old_jobs()
    cleanup_old_openart_jobs()
    requeue_expired_openart_jobs()
    recover_interrupted_jobs_on_startup()
    requeue_expired_video_jobs()

@app.get('/version')
def version():
    return {
        'ok':True,
        'system_version':SYSTEM_VERSION,
        'server_version':SYSTEM_VERSION,
        'runtime_id':RUNTIME_ID[:8],
        'direct_drive_recommended':True,
        'selective_image_jobs':True,
        'existing_image_prompts':True,
        'openart_idempotent_submission':True,'openart_request_lookup':True,'openart_resume_protocol':1,
        'qa_only_jobs':True,
        'prompt_only_jobs':True,'openart_auto_queue':True,
        'video_queue_enabled':ENABLE_VIDEO_QUEUE,
        'default_queue_video_job':DEFAULT_QUEUE_VIDEO
    }

@app.get('/auth-check')
def auth_check(authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    return {
        'ok':True,'authenticated':True,'server_version':SYSTEM_VERSION,
        'bridge_token_set':bool(BRIDGE_TOKEN),'bridge_token_length':len(BRIDGE_TOKEN),
        'openai_key_set':bool(OPENAI_API_KEY),'openai_client_ready':bool(client),
        'selective_image_jobs':True,'qa_only_jobs':True,'prompt_only_jobs':True,
        'existing_image_prompts':True,
        'openart_idempotent_submission':True,'openart_request_lookup':True,'openart_resume_protocol':1,
        'storage_persistent':STORAGE_PERSISTENT,'persistent_storage':STORAGE_PERSISTENT,
        'persistent_storage_configured':STORAGE_PERSISTENT,'data_dir':str(APP_DIR),
        'default_queue_video':DEFAULT_QUEUE_VIDEO,'default_queue_video_job':DEFAULT_QUEUE_VIDEO,
        'video_queue_enabled':ENABLE_VIDEO_QUEUE,'auto_recover_interrupted_jobs':AUTO_RECOVER_INTERRUPTED_JOBS,
        'last_image_error':LAST_IMAGE_ERROR,'last_job_error':LAST_JOB_ERROR,
        'openart_queue_enabled':ENABLE_OPENART_QUEUE,'message':'Bridge token authentication succeeded'
    }

@app.get('/openai-check')
def openai_check(authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)

    result={
        'ok':False,
        'server_version':SYSTEM_VERSION,
        'model':TEXT_MODEL,
        'openai_key_set':bool(OPENAI_API_KEY),
        'openai_client_ready':bool(client),
        'response':'',
        'error_type':'',
        'error_code':'',
        'message':''
    }

    if not OPENAI_API_KEY:
        result['error_type']='configuration'
        result['message']='OPENAI_API_KEY is not set'
        return result

    if not client:
        result['error_type']='configuration'
        result['message']='OpenAI client is not ready'
        return result

    try:
        r=client.responses.create(
            model=TEXT_MODEL,
            input='Reply with exactly: OK',
            max_output_tokens=16
        )

        result['ok']=True
        result['response']=getattr(r,'output_text','') or 'OK'
        return result

    except Exception as e:
        result['error_type']=type(e).__name__
        result['message']=str(e)

        # OpenAI SDK exceptions often expose structured error details.
        body=getattr(e,'body',None)
        if isinstance(body,dict):
            err=body.get('error',body)
            if isinstance(err,dict):
                result['error_code']=str(err.get('code') or '')
                result['message']=str(err.get('message') or result['message'])

        code=getattr(e,'code',None)
        if code and not result['error_code']:
            result['error_code']=str(code)

        return result

@app.get('/system-info')
def system_info(authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    return health()

@app.get('/health')
def health():
    waiting=rendering=processing=failed=interrupted=0
    job_count=0
    for p in JOBS_DIR.glob('*.json'):
        try:
            s=str(read_json_file(p).get('status') or '')
            job_count+=1
            waiting += 1 if s=='WAITING_VIDEO' else 0
            rendering += 1 if s=='VIDEO_RENDERING' else 0
            processing += 1 if s in ('PENDING','PROCESSING') else 0
            failed += 1 if s in ('FAILED','IMAGE_ERROR','QUALITY_REVIEW') else 0
            interrupted += 1 if s=='INTERRUPTED' else 0
        except Exception:
            pass

    openart_counts={'QUEUED':0,'CLAIMED':0,'RUNNING':0,'DOWNLOADING':0,'COMPLETED':0,'FAILED':0,'CANCELLED':0}
    for p in OPENART_JOBS_DIR.glob('*.json'):
        try:
            st=str(read_json_file(p).get('status') or '').upper()
            openart_counts[st]=openart_counts.get(st,0)+1
        except Exception:pass
    worker=parse_openart_worker_state();worker_ts=float(worker.get('updated_ts') or 0)
    worker['online']=bool(worker_ts and time.time()-worker_ts<=OPENART_WORKER_OFFLINE_SECONDS)

    return {
        'ok':True,
        'server_version':SYSTEM_VERSION,
        'text_model':TEXT_MODEL,'image_model':IMAGE_MODEL,
        'openai_key_set':bool(OPENAI_API_KEY),'openai_client_ready':bool(client),
        'image_generation':ENABLE_IMAGE_GEN,'scene_image_generation':ENABLE_SCENE_IMAGE_GEN,
        'character_reference_support':True,'image_quality_check_support':True,
        'strict_image_gate':True,'strict_anatomy_gate':True,'two_pass_image_qa':True,'selective_image_jobs':True,'qa_only_jobs':True,'prompt_only_jobs':True,
        'direct_drive_recommended':True,'direct_drive_mode':True,
        'default_queue_video':DEFAULT_QUEUE_VIDEO,'default_queue_video_job':DEFAULT_QUEUE_VIDEO,
        'video_queue_enabled':ENABLE_VIDEO_QUEUE,'auto_recover_interrupted_jobs':AUTO_RECOVER_INTERRUPTED_JOBS,
        'video_job_lease_seconds':VIDEO_JOB_LEASE_SECONDS,'job_retention_days':JOB_RETENTION_DAYS,
        'max_concurrent_jobs':MAX_CONCURRENT_JOBS,'runtime_id':RUNTIME_ID[:8],
        'storage_persistent':STORAGE_PERSISTENT,'persistent_storage':STORAGE_PERSISTENT,
        'persistent_storage_configured':STORAGE_PERSISTENT,'data_dir':str(APP_DIR),
        'storage_warning':'' if STORAGE_PERSISTENT else 'Set AI_BRIDGE_DATA_DIR to a persistent disk path to prevent Job loss after redeploy.',
        'public_base_url_set':bool(PUBLIC_BASE_URL),
        'bridge_token_set':bool(BRIDGE_TOKEN),'bridge_token_length':len(BRIDGE_TOKEN),
        'last_image_error':LAST_IMAGE_ERROR,'last_job_error':LAST_JOB_ERROR,
        'jobs_total':job_count,'jobs_processing':processing,'jobs_failed_or_review':failed,
        'jobs_interrupted':interrupted,
        'openart_queue_enabled':ENABLE_OPENART_QUEUE,
        'openart_idempotent_submission':True,'openart_request_lookup':True,'openart_resume_protocol':1,
        'openart_reconciliation_required':openart_counts.get('NEEDS_RECONCILIATION',0),
        'openart_queued':openart_counts.get('QUEUED',0),
        'openart_running':openart_counts.get('CLAIMED',0)+openart_counts.get('SUBMITTING',0)+openart_counts.get('RUNNING',0)+openart_counts.get('DOWNLOADING',0),
        'openart_completed':openart_counts.get('COMPLETED',0),
        'openart_failed':openart_counts.get('FAILED',0),
        'openart_cancelled':openart_counts.get('CANCELLED',0),
        'openart_worker':worker,
        'openart_storage_warning':'' if STORAGE_PERSISTENT else 'Bridge Persistent Disk 권장: OpenArt 요청/결과가 재배포 시 유실될 수 있습니다.',
        'video_waiting':waiting,'video_rendering':rendering
    }

