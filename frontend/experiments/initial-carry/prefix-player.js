/** Local generic-key counterpart of the published segment player, for an isolated preview. */
export class PrefixPlayer {
  constructor(context,buffers,notify){this.context=context;this.buffers=buffers;this.notify=notify;this.track='baseline';this.position=0;this.playing=false;this.node=null;this.epoch=0;this.pending=false;this.disposed=false;this.end=4;}
  snapshot(){return {track:this.track,playing:this.playing,position:this.playing?Math.min(this.end,this.startedPosition+Math.max(0,this.context.currentTime-this.startedAt)):this.position};}
  emit(){if(!this.disposed)this.notify(this.snapshot());}
  halt(){this.epoch++;this.pending=false;if(this.node){this.node.onended=null;this.node.stop();this.node.disconnect();this.node=null;}this.playing=false;}
  async play(){if(this.disposed)return;const position=this.snapshot().position;this.halt();const token=this.epoch;
    try{await this.context.resume();if(token!==this.epoch||this.disposed)return;if(this.context.state!=='running')throw Error('再生を開始できません');
      const offset=position>=this.end?0:position,node=this.context.createBufferSource();this.node=node;node.buffer=this.buffers[this.track];if(!node.buffer)throw Error('音源がありません');node.connect(this.context.destination);this.startedAt=this.context.currentTime;this.startedPosition=offset;
      node.onended=()=>{if(this.disposed||token!==this.epoch||this.node!==node)return;this.node=null;node.disconnect();this.playing=false;this.position=this.end;this.emit();};
      node.start(0,offset);node.stop(this.context.currentTime+this.end-offset);this.playing=true;this.position=offset;this.emit();
    }catch(e){if(token!==this.epoch||this.disposed)return;this.halt();this.emit();throw e;}}
  pause(){const position=this.snapshot().position;this.halt();this.position=position;this.emit();}
  stop(){this.halt();this.position=0;this.emit();}
  seek(position){if(!Number.isFinite(position)||position<0||position>this.end)throw Error('試聴区間外です');this.halt();this.position=position;this.emit();}
  async select(track){if(!this.buffers[track])throw Error('比較音源がありません');const previous=this.snapshot(),resume=previous.playing||this.pending;this.halt();this.track=track;this.position=previous.position;this.emit();if(resume){const p=this.play(),token=this.epoch;this.pending=true;try{await p;}finally{if(token===this.epoch)this.pending=false;}}}
  dispose(){this.halt();this.disposed=true;}
}
