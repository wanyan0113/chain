// Experimental API probe. Does not replace the production HermiT CLI.
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import org.semanticweb.owlapi.apibinding.OWLManager;
import org.semanticweb.owlapi.model.*;
import org.semanticweb.owlapi.reasoner.*;
import org.semanticweb.HermiT.Reasoner;

public final class OwlTimingProbe {
    static final PrintWriter out=new PrintWriter(new OutputStreamWriter(System.out,StandardCharsets.UTF_8),true);
    static void mark(int i,String label,long start) {out.println("M\t"+i+"\t"+label+"\t"+(System.nanoTime()-start));}
    public static void main(String[] args) throws Exception {
        out.println("READY\t"+ProcessHandle.current().pid());
        // Waiting for GO lets the parent directly observe readiness before work begins.
        new BufferedReader(new InputStreamReader(System.in,StandardCharsets.UTF_8)).readLine();
        List<String> subjects=Files.readAllLines(Paths.get(args[1]),StandardCharsets.UTF_8);
        List<String> predicates=Files.readAllLines(Paths.get(args[2]),StandardCharsets.UTF_8);
        int repeats=Integer.parseInt(args[3]);
        for(int i=0;i<repeats;i++) {
            long batch=System.nanoTime(),t=System.nanoTime();
            OWLOntologyManager manager=OWLManager.createOWLOntologyManager();mark(i,"java_manager_create",t);
            t=System.nanoTime();OWLOntology ontology=manager.loadOntologyFromOntologyDocument(new File(args[0]));
            mark(i,"java_ontology_load",t);
            t=System.nanoTime();OWLReasoner reasoner=new Reasoner(ontology);mark(i,"java_reasoner_construct",t);
            try {
                t=System.nanoTime();boolean consistent=reasoner.isConsistent();mark(i,"java_consistency",t);
                if(!consistent)throw new IllegalStateException("inconsistent ontology");
                t=System.nanoTime();reasoner.precomputeInferences(InferenceType.CLASS_HIERARCHY,
                    InferenceType.OBJECT_PROPERTY_HIERARCHY,InferenceType.DATA_PROPERTY_HIERARCHY,
                    InferenceType.CLASS_ASSERTIONS);mark(i,"java_precompute",t);
                t=System.nanoTime();SortedSet<String> rows=new TreeSet<>();
                OWLDataFactory df=manager.getOWLDataFactory();
                for(String s:subjects) {
                    OWLNamedIndividual individual=df.getOWLNamedIndividual(IRI.create(s));
                    for(OWLClass c:reasoner.getTypes(individual,false).getFlattened())
                        if(!c.isOWLThing())rows.add("T\t"+i+"\t"+s+"\t"+c.getIRI());
                    for(String p:predicates)
                        for(OWLNamedIndividual o:reasoner.getObjectPropertyValues(individual,
                            df.getOWLObjectProperty(IRI.create(p))).getFlattened())
                            rows.add("F\t"+i+"\t"+s+"\t"+p+"\t"+o.getIRI());
                }
                mark(i,"java_query_and_format",t);
                t=System.nanoTime();for(String row:rows)out.println(row);mark(i,"java_output_write",t);
            } finally {
                t=System.nanoTime();reasoner.dispose();manager.removeOntology(ontology);mark(i,"java_dispose",t);
            }
            mark(i,"java_batch",batch);out.println("END\t"+i);
        }
    }
}
