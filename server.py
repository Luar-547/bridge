"""
2060 SOUND ARCHIVE - GPT Bridge Server v56
"""
from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, Dict, Any
from pathlib import Path
from uuid import uuid4
from datetime import datetime
import base64, json, os, threading
try:
    from openai import OpenAI
except Exception:
    OpenAI=None
APP_DIR=Path(os.getenv('AI_BRIDGE_DATA_DIR','./ai_bridge_data')).resolve(); APP_DIR.mkdir(parents=True,exist_ok=True)
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
LAST_IMAGE_ERROR=''
client=OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None
app=FastAPI(title='2060 SOUND ARCHIVE GPT Bridge v56')
app.mount('/files',StaticFiles(directory=str(IMAGES_DIR)),name='files')

class JobRequest(BaseModel):
    record:str; title:str; message:Optional[str]=''; story:Optional[str]=''; genre:Optional[str]=''; mood:Optional[str]=''; vocal:Optional[str]=''; symbol:Optional[str]=''; thumb_composition:Optional[str]=''; source_title:Optional[str]=''; source_url:Optional[str]=''; source_genre:Optional[str]=''; song_type:Optional[str]=''; target_character:Optional[str]='';
    visual_concept:Optional[str]=''; character_lock:Optional[str]=''; background_style:Optional[str]=''; negative_elements:Optional[str]=''; base_image_rules:Optional[str]='';
    thumbnail_boost:Optional[str]=''; scene_boost:Optional[str]=''; intro_boost:Optional[str]=''; verse_boost:Optional[str]=''; pre_boost:Optional[str]=''; chorus_boost:Optional[str]=''; bridge_boost:Optional[str]=''; final_boost:Optional[str]=''; outro_boost:Optional[str]='';
    requested_by:Optional[str]=''; job_type:Optional[str]='텍스트+이미지+영상'; generate_thumbnail:bool=True; generate_motion_prompts:bool=True; queue_video_job:bool=True
class VideoCompleteRequest(BaseModel):
    mv_video_url:str; short_hook_url:Optional[str]=''; short_chorus_url:Optional[str]=''; short_final_url:Optional[str]=''; note:Optional[str]=''
class VideoFailRequest(BaseModel):
    note:str

def check_auth(h):
    if not BRIDGE_TOKEN:return
    if (h or '').replace('Bearer ','').strip()!=BRIDGE_TOKEN:raise HTTPException(status_code=401,detail='Invalid token')
def job_path(j):return JOBS_DIR/f'{j}.json'
def queue_path(j):return VIDEO_JOBS_DIR/f'{j}.json'
def save_job(d):
    d['updated_at']=datetime.now().isoformat(timespec='seconds'); job_path(d['job_id']).write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
def load_job(j):
    p=job_path(j)
    if not p.exists():raise HTTPException(status_code=404,detail='Job not found')
    return json.loads(p.read_text(encoding='utf-8'))
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
        'No logo, no watermark, no distorted hands, no extra fingers.',
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
def gen_image(p,record,suffix='thumbnail'):
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
        r=client.images.generate(
            model=IMAGE_MODEL,
            prompt=p,
            size='1536x1024'
        )

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
            import urllib.request
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
        'Create a 16:9 cinematic anime music-video keyframe.',
        'Adult character only.',
        'Keep one consistent protagonist design across all scenes: same face, hairstyle, eye color, outfit, accessories, body proportions, and color palette.',
        'Premium detailed anime illustration with realistic cinematic lighting and strong depth.',
        'No text, no logo, no watermark, no extra limbs, no distorted hands.',
        context(d), prompt_tuning(d), scene_notes.get(scene,''), scene_boost_for(d,scene),
        'Motion intent: '+motion_prompt
    ]
    return ' '.join(x for x in parts if x).strip()

def gen_scene_images(d,sp):
    if not ENABLE_SCENE_IMAGE_GEN:
        return {},{'CONFIG':'ENABLE_SCENE_IMAGE_GEN=false'}

    urls={}
    errors={}

    for key in ['INTRO','VERSE','PRE','CHORUS','BRIDGE','FINAL','OUTRO']:
        p=scene_image_prompt(d,key,sp.get(key,''))
        url,error=gen_image(p,d.record,f'scene_{key}')
        if url:
            urls[key]=url
        if error:
            errors[key]=error

    return urls,errors

def process_job(job_id):
    job=load_job(job_id)
    d=JobRequest(**job['request'])
    job['status']='PROCESSING'
    save_job(job)

    tp=call_text(thumb_prompt(d))
    description=call_text(desc_prompt(d))
    cm=common_motion(d)
    sp=scenes(d)

    thumb=''
    thumb_error=''
    if d.generate_thumbnail:
        thumb,thumb_error=gen_image(tp,d.record,'thumbnail')

    scene_urls,scene_errors=gen_scene_images(d,sp)
    generated_count=len(scene_urls)

    err_parts=[]
    if thumb_error:
        err_parts.append('THUMB: '+thumb_error)
    if scene_errors:
        for k,v in list(scene_errors.items())[:3]:
            err_parts.append(f'{k}: {v}')
        if len(scene_errors)>3:
            err_parts.append(f'+{len(scene_errors)-3} more')
    err_summary=' | '.join(err_parts)

    result={
        'thumbnail_prompt':tp,
        'thumbnail_image_url':thumb,
        'generated_description':description,
        'common_motion_prompt':cm,
        'scene_prompts':sp,
        'scene_image_urls':scene_urls,
        'scene_image_errors':scene_errors,
        'scene_images_generated':generated_count,
        'image_errors_summary':err_summary[:1500],
        'mv_prompt_status':'완료' if d.generate_motion_prompts else '',
        'mv_video_url':'',
        'short_hook_url':'',
        'short_chorus_url':'',
        'short_final_url':'',
        'note':''
    }
    job['result']=result

    if d.queue_video_job:
        q={
            'job_id':job_id,
            'record':d.record,
            'title':d.title,
            'common_motion_prompt':cm,
            'scene_prompts':sp,
            'scene_image_urls':scene_urls,
            'scene_image_errors':scene_errors,
            'scene_images_generated':generated_count,
            'created_at':datetime.now().isoformat(timespec='seconds'),
            'status':'WAITING_VIDEO'
        }
        queue_path(job_id).write_text(json.dumps(q,ensure_ascii=False,indent=2),encoding='utf-8')
        job['status']='WAITING_VIDEO'
        result['note']=f'프롬프트 완료 / 장면 이미지 {generated_count}/7 생성 / Colab Worker 대기'
        if err_summary:
            result['note']+=' / 이미지 생성 오류 있음'
    else:
        job['status']='DONE'
        result['note']=f'텍스트/이미지 완료 / 장면 이미지 {generated_count}/7'

    save_job(job)

@app.post('/jobs')
def create_job(payload:JobRequest,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization); jid=uuid4().hex; job={'job_id':jid,'status':'PENDING','created_at':datetime.now().isoformat(timespec='seconds'),'request':payload.model_dump()}; save_job(job); threading.Thread(target=process_job,args=(jid,),daemon=True).start(); return {'job_id':jid,'status':'전송완료','note':'GPT Bridge 작업 접수 완료'}
@app.get('/jobs/{job_id}')
def get_job(job_id:str,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization); j=load_job(job_id); r=j.get('result',{}); return {'job_id':j['job_id'],'status':j['status'],'thumbnail_prompt':r.get('thumbnail_prompt',''),'thumbnail_image_url':r.get('thumbnail_image_url',''),'generated_description':r.get('generated_description',''),'common_motion_prompt':r.get('common_motion_prompt',''),'scene_prompts':r.get('scene_prompts',{}),'scene_image_urls':r.get('scene_image_urls',{}),'scene_image_errors':r.get('scene_image_errors',{}),'scene_images_generated':r.get('scene_images_generated',0),'image_errors_summary':r.get('image_errors_summary',''),'mv_prompt_status':r.get('mv_prompt_status',''),'mv_video_url':r.get('mv_video_url',''),'short_hook_url':r.get('short_hook_url',''),'short_chorus_url':r.get('short_chorus_url',''),'short_final_url':r.get('short_final_url',''),'note':r.get('note','')}
@app.get('/video-jobs/next')
def next_video_job(authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    for p in sorted(VIDEO_JOBS_DIR.glob('*.json'),key=lambda x:x.stat().st_mtime):
        d=json.loads(p.read_text(encoding='utf-8')); j=load_job(d['job_id'])
        if j.get('status')=='WAITING_VIDEO':
            j['status']='VIDEO_RENDERING'; j.setdefault('result',{})['note']='Colab Worker가 영상 작업을 가져갔습니다.'; save_job(j); d['status']='VIDEO_RENDERING'; p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); return d
    return {'job_id':'','status':'EMPTY'}
@app.post('/video-jobs/{job_id}/complete')
def complete_video_job(job_id:str,payload:VideoCompleteRequest,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization); j=load_job(job_id); r=j.setdefault('result',{}); r['mv_video_url']=payload.mv_video_url; r['short_hook_url']=payload.short_hook_url or ''; r['short_chorus_url']=payload.short_chorus_url or ''; r['short_final_url']=payload.short_final_url or ''; r['note']=payload.note or '영상 렌더 완료'; j['status']='DONE'; save_job(j); return {'ok':True,'status':'DONE'}
@app.post('/video-jobs/{job_id}/fail')
def fail_video_job(job_id:str,payload:VideoFailRequest,authorization:Optional[str]=Header(default=None)):
    check_auth(authorization); j=load_job(job_id); j['status']='FAILED'; j.setdefault('result',{})['note']=payload.note; save_job(j); return {'ok':True,'status':'FAILED'}

@app.get('/auth-check')
def auth_check(authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)
    return {
        'ok':True,
        'authenticated':True,
        'bridge_token_set':bool(BRIDGE_TOKEN),
        'bridge_token_length':len(BRIDGE_TOKEN),
        'openai_key_set':bool(OPENAI_API_KEY),
        'openai_client_ready':bool(client),
        'last_image_error':LAST_IMAGE_ERROR,
        'message':'Bridge token authentication succeeded'
    }


@app.get('/openai-check')
def openai_check(authorization:Optional[str]=Header(default=None)):
    check_auth(authorization)

    result={
        'ok':False,
        'server_version':'v56',
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

@app.get('/health')
def health():
    waiting=rendering=0
    for p in JOBS_DIR.glob('*.json'):
        try:
            s=json.loads(p.read_text(encoding='utf-8')).get('status')
            waiting += 1 if s=='WAITING_VIDEO' else 0
            rendering += 1 if s=='VIDEO_RENDERING' else 0
        except:
            pass

    return {
        'ok':True,
        'server_version':'v56',
        'text_model':TEXT_MODEL,
        'image_model':IMAGE_MODEL,
        'openai_key_set':bool(OPENAI_API_KEY),
        'openai_client_ready':bool(client),
        'image_generation':ENABLE_IMAGE_GEN,
        'scene_image_generation':ENABLE_SCENE_IMAGE_GEN,
        'public_base_url_set':bool(PUBLIC_BASE_URL),
        'bridge_token_set':bool(BRIDGE_TOKEN),
        'bridge_token_length':len(BRIDGE_TOKEN),
        'last_image_error':LAST_IMAGE_ERROR,
        'video_waiting':waiting,
        'video_rendering':rendering
    }

