"""Render the second competition cut with continuously animated compositions."""
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
import imageio_ffmpeg

P = Path(__file__).resolve().parent
W,H,FPS = 1280,720,24
Y='#eaff34'; WHITE='#f5f6f8'; GRAY='#a5adba'; PURPLE='#aa89ff'
FF=imageio_ffmpeg.get_ffmpeg_exe()
FONTS={}
def f(n,b=False):
    key=(n,b)
    if key not in FONTS:FONTS[key]=ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf' if b else 'C:/Windows/Fonts/arial.ttf',n)
    return FONTS[key]
def ease(x):
    x=max(0,min(1,x));return 1-(1-x)**3
def txt(d,xy,s,n=28,color=WHITE,b=False):d.text(xy,s,font=f(n,b),fill=color)
def card(d,box,h,sub='',accent=False):
    d.rounded_rectangle(box,18,fill='#242a24' if accent else '#171c25',outline=Y if accent else '#394153',width=2)
    x,y,_,_=box;txt(d,(x+20,y+20),h,25,Y if accent else WHITE,True)
    if sub: d.multiline_text((x+20,y+62),sub,font=f(17),fill=GRAY,spacing=8)
def link(d,a,b,t):
    d.line((*a,*b),fill='#4e586d',width=3)
    for k in range(3):
        q=(t*.38+k/3)%1;x=a[0]+(b[0]-a[0])*q;y=a[1]+(b[1]-a[1])*q
        d.ellipse((x-5,y-5,x+5,y+5),fill=Y)
def image_card(im,src,box,mode='contain'):
    x,y,w,h=map(int,box)
    if w<2 or h<2:return
    fit=ImageOps.fit(src,(w,h)) if mode=='cover' else ImageOps.contain(src,(w,h))
    panel=Image.new('RGB',(w,h),'#171c25');panel.paste(fit,((w-fit.width)//2,(h-fit.height)//2))
    mask=Image.new('L',(w,h));ImageDraw.Draw(mask).rounded_rectangle((0,0,w,h),20,fill=255)
    im.paste(panel,(x,y),mask)

def main():
    desktop=Image.open(P.parent/'images/generate-desktop.png').convert('RGB')
    phone=Image.open(P.parent/'images/generate-phone.png').convert('RGB')
    setup=Image.open(P/'captures/onboarding.png').convert('RGB')
    hero=Image.open(P/'creative-studio.png').convert('RGB')
    ends=[0,12,27,46,65,83,101,114]
    out=P/'comfy-commander-dynamic-v2.mp4'
    cmd=[FF,'-y','-f','rawvideo','-vcodec','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-', '-i',str(P/'comfy-commander-competition-demo.mp4'),'-map','0:v','-map','1:a','-vf','scale=1920:1080','-c:v','libx264','-preset','fast','-crf','19','-c:a','aac','-ar','48000','-b:a','192k','-t','114','-movflags','+faststart',str(out)]
    proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=open(P/'dynamic-render.log','w'))
    for frame in range(114*FPS):
        t=frame/FPS;scene=max(i for i in range(7) if t>=ends[i]);s=t-ends[scene];duration=ends[scene+1]-ends[scene]
        im=Image.new('RGB',(W,H),'#0b0e16');d=ImageDraw.Draw(im)
        for x in range(-80,1400,80):
            xx=x+int(t*8)%80;d.line((xx,0,xx-130,H),fill='#151b2a')
        for y in range(0,800,80):d.line((0,y,W,y),fill='#151b2a')
        for k in range(20):
            px=(k*173+t*(12+k%4))%W;py=(k*97+math.sin(t*.4+k)*30)%H
            d.ellipse((px,py,px+2,py+2),fill='#536044')
        if scene==0:
            image_card(im,hero,(380-int(s*2),40,900+int(s*4),630),'cover');d=ImageDraw.Draw(im)
            overlay=Image.new('RGBA',(W,H));od=ImageDraw.Draw(overlay)
            for x in range(W):od.line((x,0,x,H),fill=(7,10,17,int(255*max(0,1-x/1050))))
            im=Image.alpha_composite(im.convert('RGBA'),overlay).convert('RGB');d=ImageDraw.Draw(im)
            for i,(word,col) in enumerate([('COMFY',Y),('COMMANDER',WHITE)]):
                off=int((1-ease(s-i*.22))*90);txt(d,(65,190+i*80+off),word,68,col,True)
            txt(d,(68,385),'by Tensor Master',26,GRAY)
            txt(d,(68,465),'ComfyUI power.',36,WHITE,True);txt(d,(68,512),'Ready for your next idea.',31,Y)
        elif scene==1:
            txt(d,(60,90),'YOUR STUDIO. EVERYWHERE.',45,WHITE,True)
            if s<5:
                image_card(im,setup,(60,185+int(35*(1-ease(s))),1160,440))
                txt(d,(65,650),'Guided setup  /  Bring your keys  /  Choose your compute',23,Y)
            else:
                q=ease(s-5);image_card(im,desktop,(55-int(15*math.sin(s*.3)),210,900,440))
                image_card(im,phone,(990+int((1-q)*250),155+int(math.sin(s)*8),235,490))
                for i,word in enumerate(['DESKTOP','REMOTE','PHONE']):
                    txt(d,(70+i*310,155),word,20,Y if int(s)%3==i else GRAY,True)
        elif scene==2:
            txt(d,(60,90),'EVERY JOB. UNDER CONTROL.',43,WHITE,True)
            link(d,(305,390),(480,390),s);link(d,(785,340),(965,255),s);link(d,(785,390),(965,420),s);link(d,(785,440),(965,585),s)
            card(d,(60,300,305,485),'CREATIVE BRIEF','Prompt + references\nProfile + settings')
            card(d,(480,270,785,520),'DISPATCHER','Queue / Priority\nCompatible routing\nRecovery / Outputs',True)
            for i,h in enumerate(['LOCAL GPU','GPU CLUSTER','CLOUD WORKER']):
                off=int(70*(1-ease(s-i*.4)));card(d,(965+off,185+i*165,1220+off,320+i*165),h,'RUNNING' if (int(s)+i)%4<3 else 'COMPLETE')
            for i in range(4):
                x=65+i*200;y=590+int(8*math.sin(s*2+i));card(d,(x,y,x+180,y+65),f'JOB {i+1:02d}')
        elif scene==3:
            txt(d,(60,85),'ELASTIC COMPUTE',49,WHITE,True)
            txt(d,(62,150),'Capacity at your fingertips.',31,Y)
            count=min(12,1+int(s*1.2))
            for i in range(12):
                x=65+(i%6)*200;y=280+(i//6)*165;active=i<count
                scale=ease(s-i*.3);off=int(45*(1-scale))
                card(d,(x,y+off,x+175,y+off+130),f'GPU {i+1:02d}','ONLINE' if active else 'AVAILABLE',active)
                if active:
                    for j in range(9):d.rectangle((x+20+j*15,y+100,x+30+j*15,y+106),fill=Y if j<(int(s*3+i)%9) else '#455047')
            txt(d,(65,640),'Local + RunPod + Vast  |  Your budget. Your capacity.',24,GRAY)
        elif scene==4:
            txt(d,(60,80),'FOCUS ON THE SHOT.',47,WHITE,True)
            image_card(im,hero,(670,180,550,430),'cover')
            labels=[('MiniMax H3','Reference video'),('Fast H3','Fast iteration'),('Qwen Image','Design + editing'),('Turbo presets','4 / 8 steps')]
            for i,(h,sub) in enumerate(labels):
                x=65+(i%2)*295;y=200+(i//2)*170;off=int((1-ease(s-i*.25))*80)
                card(d,(x-off,y,x+270-off,y+140),h,sub,i==int(s/3)%4)
            if s>6:
                d.rounded_rectangle((65,590,1215,675),20,fill='#222b20',outline=Y,width=2)
                for i,h in enumerate(['SOURCE','SAM 3','TARGET GUIDE','H3 RESULT']):
                    txt(d,(95+i*285,619),h,22,Y,True)
                    if i<3:link(d,(260+i*285,632),(335+i*285,632),s)
            txt(d,(1010,560),'Concept imagery',14,GRAY)
        elif scene==5:
            txt(d,(60,85),'YOUR AGENT. YOUR STUDIO.',44,WHITE,True)
            card(d,(65,220,535,580),'CODEX / MCP CLIENT','> Inspect available workers\n\n> Prepare a shot list\n\n> Submit authorized jobs\n\n> Monitor progress',True)
            link(d,(535,380),(720,380),s)
            card(d,(720,225,1215,575),'COMMANDER API','MCP tools + controller API\n\nPolicy + confirmation\n\nLocal and cloud workers')
            messages=['WORKERS DISCOVERED','PROFILE VALIDATED','JOB PREPARED','STATUS RECEIVED']
            msg=messages[int(s/2)%4];txt(d,(745,620),msg,23,Y,True)
        else:
            image_card(im,hero,(0,0,1280,720),'cover')
            shade=Image.new('RGBA',(W,H),(5,8,14,155));im=Image.alpha_composite(im.convert('RGBA'),shade).convert('RGB');d=ImageDraw.Draw(im)
            txt(d,(65,110),'CREATIVITY FIRST.',64,WHITE,True)
            txt(d,(68,210),'Scalable compute',51,Y,True);txt(d,(68,272),'at your fingertips.',51,Y,True)
            txt(d,(68,420),'COMFY COMMANDER',40,WHITE,True)
            txt(d,(70,475),'by Tensor Master',26,GRAY)
            txt(d,(70,585),'github.com/mexxmillion/comfy-commander',25,WHITE)
            txt(d,(70,635),'#OpenSourceHiggsfield   /   AGPL-3.0',22,Y)
        d=ImageDraw.Draw(im)
        d.rectangle((0,H-5,int(W*t/114),H),fill=Y)
        txt(d,(60,30),'CC  /  COMFY COMMANDER',15,Y,True)
        txt(d,(1050,30),f'{scene+1:02d}  /  07',15,GRAY)
        # Fade each chapter from black, with a short final fade.
        fade=min(1,s/.35,(114-t)/.6)
        if fade<1:im=Image.blend(Image.new('RGB',(W,H),'#080b12'),im,max(0,fade))
        if frame% (FPS*10)==0:print(f'{t:.0f}/114 seconds',flush=True)
        if frame in [FPS*5,FPS*20,FPS*37,FPS*55,FPS*75,FPS*92,FPS*108]:im.save(P/f'v2-preview-{int(t):03d}.jpg')
        proc.stdin.write(im.tobytes())
    proc.stdin.close()
    if proc.wait():raise SystemExit('ffmpeg failed; see dynamic-render.log')
    print(out)
if __name__=='__main__':main()
