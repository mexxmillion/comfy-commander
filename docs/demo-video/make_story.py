"""A playful one-minute product story. Uses real UI and illustrative diagrams."""
import math
import subprocess
from PIL import Image, ImageDraw, ImageOps
from make_dynamic import P,W,H,FPS,FF,Y,WHITE,GRAY,txt,card,link,image_card,ease

def creator(d,x,y,t,stressed=False):
    d.ellipse((x-32,y-82,x+32,y-18),fill='#b29aff',outline=WHITE,width=3)
    d.rounded_rectangle((x-43,y-12,x+43,y+110),20,fill='#393454',outline='#b29aff',width=3)
    d.line((x-14,y+110,x-25,y+160),fill=WHITE,width=8);d.line((x+14,y+110,x+25,y+160),fill=WHITE,width=8)
    d.ellipse((x-15,y-57,x-10,y-52),fill='#111827');d.ellipse((x+10,y-57,x+15,y-52),fill='#111827')
    d.arc((x-13,y-54,x+13,y-30),0 if stressed else 10,180,fill='#111827',width=3)
    if stressed:txt(d,(x+35,y-100),'?!',35,Y,True)
    d.line((x-40,y+20,x-90,y+50+int(math.sin(t*4)*8)),fill=WHITE,width=7)
    d.line((x+40,y+20,x+90,y-10+int(math.sin(t*4)*8)),fill=WHITE,width=7)

def main():
    total=60
    desktop=Image.open(P.parent/'images/generate-desktop.png').convert('RGB')
    phone=Image.open(P.parent/'images/generate-phone.png').convert('RGB')
    hero=Image.open(P/'creative-studio.png').convert('RGB')
    ends=[0,9,14,25,35,47,55,60]
    out=P/'comfy-commander-story-60sec.mp4'
    cmd=[FF,'-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-','-i',str(P/'story-soundtrack.m4a'),'-map','0:v','-map','1:a','-vf','scale=1920:1080','-c:v','libx264','-preset','fast','-crf','19','-c:a','aac','-ar','48000','-b:a','192k','-t','60','-movflags','+faststart',str(out)]
    proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=open(P/'story-render.log','w'))
    for frame in range(total*FPS):
        t=frame/FPS;i=max(j for j in range(7) if t>=ends[j]);s=t-ends[i]
        im=Image.new('RGB',(W,H),'#0d1020');d=ImageDraw.Draw(im)
        for k in range(18):
            x=(k*171+t*12)%W;y=(k*97+20*math.sin(t+k))%H
            d.ellipse((x,y,x+3,y+3),fill='#344453')
        if i==0:
            txt(d,(60,90),'“JUST ONE QUICK IDEA.”',48,WHITE,True)
            creator(d,190,390,t,True)
            for k,h in enumerate(['NODES','MODELS','MORE NODES','THE QUEUE']):
                x=420+(k%2)*380;y=210+(k//2)*180
                off=int(140*(1-ease(s-k*.65)))
                card(d,(x+off,y,x+off+330,y+140),h,'Loading your patience…',k==3)
            txt(d,(65,635),'Creative energy: 100%     /     Lunch break: endangered',23,Y)
        elif i==1:
            txt(d,(60,85),'MEANWHILE, YOUR PHONE…',44,WHITE,True)
            d.rounded_rectangle((495,180,785,600),38,fill='#20263a',outline='#b29aff',width=4)
            d.arc((580,320,700,440),s*150,s*150+250,fill=Y,width=10)
            txt(d,(530,480),'Still waiting?',28,WHITE,True)
            txt(d,(90,625),'There has to be a better way.',32,Y,True)
        elif i==2:
            txt(d,(60,80),'MEET COMFY COMMANDER.',45,Y,True)
            image_card(im,desktop,(60,185+int(8*math.sin(s)),900,430))
            image_card(im,phone,(990+int(200*(1-ease(s-.4))),150,225,475))
            d=ImageDraw.Draw(im)
            txt(d,(65,650),'PHONE  /  MACBOOK  /  PC     ·     No local GPU required',25,WHITE,True)
            if s>5:
                d.rounded_rectangle((680,550,1190,615),18,fill=Y)
                txt(d,(705,570),'YOUR STUDIO. GOES WITH YOU.',22,'#111420',True)
        elif i==3:
            txt(d,(60,80),'ONE BUSY WORKER? SEND HELP.',43,WHITE,True)
            card(d,(65,280,360,500),'YOUR SHOT LIST','Shot 01 / Shot 02\nShot 03 / Shot 04')
            link(d,(360,385),(475,385),s)
            card(d,(475,270,790,510),'DISPATCHER','Queue + priority\nCompatible routing',True)
            for k,h in enumerate(['GPU 01','GPU 02','GPU 03']):
                y=180+k*165;link(d,(790,385),(975,y+65),s+k*.2)
                card(d,(975,y,1220,y+130),h,'BUSY' if k==0 else 'RENDERING',k>0)
            txt(d,(65,635),'Multiple workers. Multiple jobs. Moving in parallel.',28,Y,True)
        elif i==4:
            txt(d,(60,80),'BIG IDEA. MEET MORE GPUs.',45,WHITE,True)
            for k in range(8):
                x=70+(k%4)*290;y=220+(k//4)*150;active=k<min(8,1+int(s*1.3))
                off=int(70*(1-ease(s-k*.22)))
                card(d,(x,y+off,x+260,y+125+off),f'CLOUD GPU {k+1:02d}','AVAILABLE' if not active else 'ADDED TO FLEET',active)
            txt(d,(70,565),'DIRECT GPU RENTALS  /  RUNPOD + VAST',29,Y,True)
            txt(d,(70,620),'Your imagination grows. Your budget still gets a vote.',27,WHITE,True)
        elif i==5:
            txt(d,(60,80),'CASUAL CREATOR. PRODUCTION BRAIN.',38,WHITE,True)
            creator(d,180,370,t)
            card(d,(380,220,815,545),'CODEX + MCP','Prepare approved workflows\n\nQueue production jobs\n\nMonitor progress',True)
            link(d,(815,380),(965,380),s)
            card(d,(965,245,1220,520),'GPU WORKERS','Local + rented\n\nJobs in motion')
            txt(d,(65,630),'You direct the creative work.',30,Y,True)
        else:
            image_card(im,hero,(0,0,W,H),'cover')
            im=Image.alpha_composite(im.convert('RGBA'),Image.new('RGBA',(W,H),(5,8,15,175))).convert('RGB');d=ImageDraw.Draw(im)
            txt(d,(65,100),'COMFY COMMANDER',57,WHITE,True)
            txt(d,(68,180),'by Tensor Master',27,GRAY)
            txt(d,(65,300),'CREATIVITY FIRST.',53,Y,True)
            txt(d,(68,380),'Scalable GPU compute at your fingertips.',31,WHITE,True)
            txt(d,(68,570),'github.com/mexxmillion/comfy-commander',25,WHITE)
            txt(d,(68,630),'#OpenSourceHiggsfield',23,Y)
        d=ImageDraw.Draw(im);txt(d,(60,28),'CC  /  A QUICK CREATIVE DETOUR',15,Y,True)
        d.rectangle((0,H-5,int(W*t/total),H),fill=Y)
        fade=min(1,s/.18,(total-t)/.5)
        if fade<1:im=Image.blend(Image.new('RGB',(W,H),'#0d1020'),im,max(0,fade))
        if frame%(FPS*10)==0:print(f'{t:.0f}/60 seconds',flush=True)
        if frame in [FPS*4,FPS*18,FPS*29,FPS*40,FPS*51,FPS*57]:im.save(P/f'story-preview-{int(t):02d}.jpg')
        proc.stdin.write(im.tobytes())
    proc.stdin.close()
    if proc.wait():raise SystemExit('Render failed; see story-render.log')
    print(out)
if __name__=='__main__':main()
