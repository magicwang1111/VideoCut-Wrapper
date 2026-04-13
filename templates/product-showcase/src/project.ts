import { makeProject } from '@revideo/core';
import intro from './scenes/intro?scene';
import detail from './scenes/detail?scene';
import outro from './scenes/outro?scene';

export default makeProject({
  scenes: [intro, detail, outro],
});
