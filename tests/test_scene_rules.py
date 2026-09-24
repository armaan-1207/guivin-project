import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'backend'))
from app.scene_rules import SceneEvaluator, SceneRule, crossed

class SceneRuleTests(unittest.TestCase):
    def test_geometry_validation_and_finite_tripwire(self):
        self.assertTrue(crossed((0,.5),(1,.5),(.4,.4),(.4,.6)))
        self.assertFalse(crossed((0,.5),(.2,.5),(.4,.4),(.4,.6)))
        for points in [[(0,0),(0,0)],[(0,0),(2,1)]]:
            with self.assertRaises(ValueError):
                SceneRule(id='r',kind='TRIPWIRE',points=points)
        with self.assertRaises(ValueError):
            SceneRule(id='r',kind='CROWD',points=[(0,0),(.5,.5),(1,1)])

    def test_entry_dwell_crowd_and_version_reset(self):
        evaluator = SceneEvaluator()
        region = [(0,.5),(1,.5),(1,1),(0,1)]
        rules = [SceneRule(id='entry',kind='REGION_ENTRY',points=region).model_dump(),
                 SceneRule(id='dwell',kind='LOITERING',points=region,dwell_seconds=1).model_dump(),
                 SceneRule(id='crowd',kind='CROWD',points=region,count=2).model_dump()]
        outside = {'bbox':(20,10,20,35),'class':'person','confidence':.9}
        inside = dict(outside,bbox=(20,20,20,35))
        self.assertEqual(evaluator.evaluate(rules,[outside],(100,100),0,1),[])
        events=evaluator.evaluate(rules,[inside],(100,100),.2,1)
        self.assertEqual([e['rule']['id'] for e in events],['entry'])
        events=evaluator.evaluate(rules,[inside],(100,100),1.3,1)
        self.assertEqual([e['rule']['id'] for e in events],['dwell'])
        self.assertEqual(evaluator.evaluate(rules,[inside],(100,100),1.5,1),[])
        self.assertEqual(evaluator.evaluate(rules,[inside],(100,100),1.7,2),[])
        events=evaluator.evaluate(rules,[inside,dict(inside,bbox=(70,20,20,35))],(100,100),2,2)
        self.assertEqual([e['rule']['id'] for e in events],['crowd'])
