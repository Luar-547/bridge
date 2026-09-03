"""
2060 SOUND ARCHIVE - GPT Bridge Server v78 STABLE INTEGRATED FINAL
"""
from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, Dict, Any
from pathlib import Path
from uuid import uuid4
from datetime import datetime
import base64, json, os, threading, re, urllib.request, urllib.parse, time, traceback
from concurrent.futures import ThreadPoolExecutor
try:
    from openai import OpenAI
except Exception:
    OpenAI=None
SYSTEM_VERSION='v78'
RUNTIME_ID=uuid4().hex
DATA_DIR_ENV=os.getenv('AI_BRIDGE_DATA_DIR','').strip()
APP_DIR=Path(DATA_DIR_ENV or './ai_bridge_data').resolve(); APP_DIR.mkdir(parents=True,exist_ok=True)
JOBS_DIR=APP_DIR/'jobs'; JOBS_DIR.mkdir(exist_ok=True)
IMAGES_DIR=APP_DIR/'images'; IMAGES_DIR.mkdir(exist_ok=True)
VIDEO_JOBS_DIR=APP_DIR/'video_jobs'; VIDEO_JOBS_DIR.mkdir(exist_ok=True)
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
app=FastAPI(title='2060 SOUND ARCHIVE GPT Bridge v78')
app.mount('/files',StaticFiles(directory=str(IMAGES_DIR)),name='files')

class JobRequest(BaseModel):
    record:str; title:str; message:Optional[str]=''; story:Optional[str]=''; genre:Optional[str]=''; mood:Optional[str]=''; vocal:Optional[str]=''; symbol:Optional[str]=''; thumb_composition:Optional[str]=''; source_title:Optional[str]=''; source_url:Optional[str]=''; source_genre:Optional[str]=''; song_type:Optional[str]=''; target_character:Optional[str]='';
    visual_concept:Optional[str]=''; character_lock:Optional[str]=''; background_style:Optional[str]=''; negative_elements:Optional[str]=''; base_image_rules:Optional[str]='';
    thumbnail_boost:Optional[str]=''; scene_boost:Optional[str]=''; intro_boost:Optional[str]=''; verse_boost:Optional[str]=''; pre_boost:Optional[str]=''; chorus_boost:Optional[str]=''; bridge_boost:Optional[str]=''; final_boost:Optional[str]=''; outro_boost:Optional[str]='';
    character_reference_url:Optional[str]=''; character_reference_b64:Optional[str]=''; character_reference_mime:Optional[str]=''; character_reference_name:Optional[str]='';
    quality_check:bool=True; quality_threshold:int=82; max_regenerations:int=1;
    requested_by:Optional[str]=''; job_type:Optional[str]='텍스트+이미지+영상'; generate_thumbnail:bool=True; generate_motion_prompts:bool=True; queue_video_job:Optional[bool]=None; force_new:bool=False
class VideoCompleteRequest(BaseModel):
    mv_video_url:str; short_hook_url:Optional[str]=''; short_chorus_url:Optional[str]=''; short_final_url:Optional[str]=''; note:Optional[str]=''
class VideoFailRequest(BaseModel):
    note:str

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

def gen_image(p,record,suffix='thumbnail',reference_path=None):
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
                    r=client.images.edit(model=IMAGE_MODEL,image=ref_file,prompt=ref_instruction+p,size='1536x1024')
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
        fn=f'{record}_{suffix}.png'
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
        url,error=gen_image(current_prompt,d.record,unique_suffix,reference_path)
        final_url,final_error=url,error
        if not url or error:
            break
        final_qa=quality_check_image(url,d,label,current_prompt,reference_url)
        if final_qa.get('pass',True):
            break
        if attempt<max_retry:
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

def _process_job_impl(job_id):
    job=load_job(job_id)
    d=JobRequest(**job['request'])
    d.quality_threshold=max(50,min(100,int(d.quality_threshold or 82)))
    d.max_regenerations=max(0,min(2,int(d.max_regenerations or 0)))
    job['status']='PROCESSING'; save_job(job)

    reference_path,reference_public_url=prepare_character_reference(d)
    # Do not keep large base64 payload in persistent job JSON after reference was prepared.
    if isinstance(job.get('request'),dict) and job['request'].get('character_reference_b64'):
        job['request']['character_reference_b64']=''
        save_job(job)

    tp=call_text(thumb_prompt(d))
    description=call_text(desc_prompt(d))
    cm=common_motion(d)
    sp=scenes(d)

    thumb=''; thumb_error=''; thumb_qa={'score':None,'pass':True,'issues':[],'regeneration_instruction':'','qa_error':''}; thumb_retries=0
    if d.generate_thumbnail:
        thumb,thumb_error,thumb_qa,thumb_retries=generate_with_qa(d,tp,'thumbnail','THUMBNAIL',reference_path,reference_public_url)

    scene_urls,scene_errors,scene_quality,scene_regens=gen_scene_images(d,sp,reference_path,reference_public_url)
    generated_count=len(scene_urls)
    regen_total=thumb_retries+scene_regens

    quality_report={'THUMBNAIL':thumb_qa,**scene_quality}
    numeric_scores=[float(v['score']) for v in quality_report.values() if isinstance(v,dict) and v.get('score') is not None]
    quality_average=round(sum(numeric_scores)/len(numeric_scores),1) if numeric_scores else None
    failed_quality=[k for k,v in quality_report.items() if isinstance(v,dict) and not v.get('pass',True)]
    qa_errors=[k for k,v in quality_report.items() if isinstance(v,dict) and v.get('qa_error')]
    if not d.quality_check:
        quality_status='미사용'
    elif failed_quality:
        quality_status='검토 필요'
    elif qa_errors:
        quality_status='검수 오류'
    else:
        quality_status='통과'

    err_parts=[]
    if thumb_error:err_parts.append('THUMB: '+thumb_error)
    if scene_errors:
        for k,v in list(scene_errors.items())[:3]:err_parts.append(f'{k}: {v}')
        if len(scene_errors)>3:err_parts.append(f'+{len(scene_errors)-3} more')
    err_summary=' | '.join(err_parts)

    result={
        'thumbnail_prompt':tp,'thumbnail_image_url':thumb,'generated_description':description,
        'common_motion_prompt':cm,'scene_prompts':sp,'scene_image_urls':scene_urls,
        'scene_image_errors':scene_errors,'scene_images_generated':generated_count,
        'image_errors_summary':err_summary[:1500],
        'character_reference_url':reference_public_url,
        'image_quality_status':quality_status,'image_quality_average':quality_average,
        'image_regenerations':regen_total,'image_quality_report':quality_report,
        'quality_failed_scenes':failed_quality,
        'mv_prompt_status':'완료' if d.generate_motion_prompts else '',
        'mv_video_url':'','short_hook_url':'','short_chorus_url':'','short_final_url':'','note':''
    }
    job['result']=result

    # v64 hard safety gates: image generation must be complete before QA/video queue.
    expected_thumb_ok=(not d.generate_thumbnail) or bool(thumb)
    all_scenes_ok=(generated_count==7 and not scene_errors)
    image_generation_block=bool((not expected_thumb_ok) or (not all_scenes_ok) or thumb_error)
    quality_block=bool(d.quality_check and (failed_quality or qa_errors))

    if image_generation_block or quality_block:
        try:
            qp=queue_path(job_id)
            if qp.exists():qp.unlink()
        except Exception:
            pass

    if image_generation_block:
        job['status']='IMAGE_ERROR'
        missing=[]
        if d.generate_thumbnail and not thumb:missing.append('THUMBNAIL')
        for k in ['INTRO','VERSE','PRE','CHORUS','BRIDGE','FINAL','OUTRO']:
            if not scene_urls.get(k):missing.append(k)
        result['quality_failed_scenes']=list(dict.fromkeys((failed_quality or [])+missing))
        result['image_quality_status']='검수 불가'
        result['note']=f'이미지 생성 실패 / 장면 이미지 {generated_count}/7 / Colab Worker 보류'
        if missing:result['note']+=' / 누락: '+', '.join(missing)
        if err_summary:result['note']+=' / 이미지 오류: '+err_summary[:900]
    elif quality_block:
        job['status']='QUALITY_REVIEW'
        reason=list(dict.fromkeys((failed_quality or [])+(qa_errors or [])))
        result['quality_failed_scenes']=reason
        result['note']='이미지 QA 통과 실패: '+', '.join(reason)+f' / 평균 {quality_average if quality_average is not None else "-"}점 / 자동 재생성 {regen_total}회. 3D 영상 변환은 보류했습니다.'
    elif should_queue_video(d):
        q={
            'job_id':job_id,'record':d.record,'title':d.title,'common_motion_prompt':cm,
            'scene_prompts':sp,'scene_image_urls':scene_urls,'scene_image_errors':scene_errors,
            'scene_images_generated':generated_count,'image_quality_status':quality_status,
            'image_quality_average':quality_average,'image_regenerations':regen_total,
            'created_at':datetime.now().isoformat(timespec='seconds'),'status':'WAITING_VIDEO'
        }
        save_video_queue(q)
        job['status']='WAITING_VIDEO'
        result['note']=f'프롬프트 완료 / 장면 이미지 7/7 / QA {quality_status}'
        if quality_average is not None:result['note']+=f' {quality_average:.0f}점'
        if regen_total:result['note']+=f' / 자동 재생성 {regen_total}회'
        result['note']+=' / Colab Worker 대기'
    else:
        job['status']='DONE'
        result['note']=f'텍스트/이미지 완료 / 장면 이미지 {generated_count}/7 / QA {quality_status} / Direct Drive 렌더 준비'
        if bool(getattr(d,'queue_video_job',False)) and not ENABLE_VIDEO_QUEUE:
            result['note']+=' / Bridge 영상큐는 환경설정에서 OFF'
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
    cleanup_old_jobs()
    with JSON_LOCK:
        if not payload.force_new:
            existing=find_active_job(payload.record)
            if existing:
                return {
                    'job_id':existing['job_id'],
                    'status':'전송완료',
                    'reused':True,
                    'note':'같은 곡의 진행 중 Job을 재사용했습니다.'
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

@app.on_event('startup')
def startup_event():
    cleanup_old_jobs()
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
        'storage_persistent':STORAGE_PERSISTENT,'persistent_storage':STORAGE_PERSISTENT,
        'persistent_storage_configured':STORAGE_PERSISTENT,'data_dir':str(APP_DIR),
        'default_queue_video':DEFAULT_QUEUE_VIDEO,'default_queue_video_job':DEFAULT_QUEUE_VIDEO,
        'video_queue_enabled':ENABLE_VIDEO_QUEUE,'auto_recover_interrupted_jobs':AUTO_RECOVER_INTERRUPTED_JOBS,
        'last_image_error':LAST_IMAGE_ERROR,'last_job_error':LAST_JOB_ERROR,
        'message':'Bridge token authentication succeeded'
    }

@app.get('/openai-check')
def openai_check(authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)

    result={
        'ok':False,
        'server_version':'v78',
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

    return {
        'ok':True,
        'server_version':SYSTEM_VERSION,
        'text_model':TEXT_MODEL,'image_model':IMAGE_MODEL,
        'openai_key_set':bool(OPENAI_API_KEY),'openai_client_ready':bool(client),
        'image_generation':ENABLE_IMAGE_GEN,'scene_image_generation':ENABLE_SCENE_IMAGE_GEN,
        'character_reference_support':True,'image_quality_check_support':True,
        'strict_image_gate':True,'strict_anatomy_gate':True,'two_pass_image_qa':True,
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
        'video_waiting':waiting,'video_rendering':rendering
    }

